"""Bounded desktop management operations through the existing MCP broker.

No arbitrary MCP invocation or shell command is exposed by this bridge.
"""
from __future__ import annotations

SKILL_ACTIONS = (
    'manage', 'sources', 'sync', 'find', 'load', 'resource', 'validate',
    'prepare', 'apply-local', 'states', 'toggle', 'plan-change', 'apply-change',
    'publish-plan', 'restore-local', 'publication-prepare', 'publication-apply',
)
PROVIDER_ACTIONS = ('rescan', 'reload', 'enable', 'disable')


def checked_provider_action(args):
    if set(args) != {'action', 'provider_id', 'confirmed'}:
        raise ValueError('Expected action, provider_id and confirmed')
    action = args['action']
    provider_id = args['provider_id']
    if action not in PROVIDER_ACTIONS or args['confirmed'] is not True:
        raise ValueError('Provider lifecycle changes require explicit confirmation')
    if action == 'rescan':
        if provider_id is not None:
            raise ValueError('Rescan has no provider_id')
        return 'runtime.provider_rescan', {}
    from desktop_runtime.components import PROVIDER_ID
    if not isinstance(provider_id, str) or not PROVIDER_ID.fullmatch(provider_id):
        raise ValueError('Invalid provider ID')
    return f'runtime.provider_{action}', {'provider_id': provider_id}


def checked_skill_action(args):
    if set(args) != {'action', 'arguments', 'confirmed'}:
        raise ValueError('Expected action, arguments and confirmed')
    if args['action'] not in SKILL_ACTIONS or not isinstance(args['arguments'], dict) or type(args['confirmed']) is not bool:
        raise ValueError('Unsupported Skill operation')
    return f"skill-library.{args['action']}", args['arguments']
