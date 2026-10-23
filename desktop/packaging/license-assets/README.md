# Reviewed supplemental license texts

These public texts fill omissions in otherwise pinned distribution archives. `manifest.json` binds the exact package/version, upstream source URL and SHA-256. The build copies only a matching entry and rejects changed bytes or escaping paths; it does not retrieve new licenses at build time.

Dnspython 2.9.0 and FastMCP slim 4.0.3 lack notice files in their installed wheels. Their project release texts are preserved here. Alloc-stdlib 0.3.0 and the WebView2 crates use the corresponding upstream commit from Cargo's packaged VCS metadata. Selectors 0.38.0 declares MPL-2.0 in its Cargo metadata and source headers but omits a full license text; the SPDX MPL-2.0 text is retained with a fixed local hash and its upstream URI.

The actual target's MPL source archives are copied separately from Cargo's cache and must match Cargo.lock checksums. Other dependency texts come from their actual installed distribution/registry files. This supplements the license inventory; it does not assert public-release approval or grant a license for the first-party PLA repository.
