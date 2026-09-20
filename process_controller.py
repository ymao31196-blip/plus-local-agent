"""Bounded pipe capture and lifecycle control for runtime-owned children."""
from __future__ import annotations
import codecs
import io
import os
import select
import subprocess
import tempfile
import time
from threading import Event, Thread
from runtime_context import CURRENT, checkpoint, terminate_owned_process_tree


class CapturedText(str):
    """A bounded tail carrying the length before truncation."""
    def __new__(cls, value: str, original_length: int):
        instance = super().__new__(cls, value)
        instance.original_length = original_length
        return instance


def _read_available(pipe) -> bytes | None:
    """Poll only our pipe handle so inherited descendant handles cannot hang us."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        peek.restype = wintypes.BOOL
        available = wintypes.DWORD()
        if not peek(msvcrt.get_osfhandle(pipe.fileno()), None, 0, None,
                    ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in {109, 232}:  # broken/no-data pipe: writer closed
                return b""
            raise ctypes.WinError(error)
        if not available.value:
            return None
        return os.read(pipe.fileno(), min(available.value, 8192))
    ready, _, _ = select.select([pipe], [], [], 0)
    return os.read(pipe.fileno(), 8192) if ready else None


def controlled_run(command, *, cwd, timeout, shell=False, env=None,
                   input=None, capture_output=True, text=False):
    """Internal run-shaped backend; termination uses the spawned handle only."""
    checkpoint()
    context = CURRENT.get()
    # A private seekable input avoids a blocked writer if a child ignores stdin.
    with tempfile.TemporaryFile() as source:
        if input is not None:
            source.write(input.encode("utf-8") if isinstance(input, str) else input)
            source.seek(0)
        process = subprocess.Popen(
            command, cwd=cwd, env=env, shell=False, stdin=source,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        tails, lengths, errors = ["", ""], [0, 0], []
        stop = Event()

        def read_pipe(pipe, index):
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            if text:
                decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
            try:
                while not stop.is_set():
                    block = _read_available(pipe)
                    if block is None:
                        stop.wait(.01)
                        continue
                    value = decoder.decode(block, final=not block)
                    tails[index] = (tails[index] + value)[-20_000:]
                    lengths[index] += len(value)
                    if not block:
                        return
            except Exception as exc:
                errors.append(str(exc))
            finally:
                pipe.close()

        readers = [Thread(target=read_pipe, args=(pipe, i), daemon=True)
                   for i, pipe in enumerate((process.stdout, process.stderr))]
        timed_out = False
        try:
            if context:
                context.register(process)
            for reader in readers:
                reader.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_owned_process_tree(process)
            deadline = time.monotonic() + 1
            for reader in readers:
                reader.join(max(0, deadline - time.monotonic()))
            if any(reader.is_alive() for reader in readers):
                raise RuntimeError("Descendant still owns output pipes; capture incomplete; direct child has exited")
            if errors:
                raise RuntimeError("Process output capture failed: " + errors[0])
            outputs = [CapturedText(tails[i], lengths[i]) for i in range(2)]
            if timed_out:
                raise subprocess.TimeoutExpired(command, timeout, output=outputs[0], stderr=outputs[1])
            return subprocess.CompletedProcess(command, process.returncode, *outputs)
        finally:
            stop.set()
            if process.poll() is None:
                terminate_owned_process_tree(process)
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=1)
            if context:
                context.unregister(process)
