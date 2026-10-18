# Third-party software and benchmark material

The [MIT license](LICENSE) covers OSD-Bench's original code. It does not relicense
third-party software, images, tasks, dataset content or captured site templates.
The release tarball does not bundle the virtual environment, Chromium or Docker
images. The template HTML in `catalog/templates/` is adapted from benchmark site
pages (scrubbed and modified to display errors); retain applicable upstream terms.

Installed Python versions below were checked with `importlib.metadata` in this
repository's `.venv` on 2026-10-10. Setup currently has unpinned dependencies;
these versions describe this environment, not a lockfile. No running container
was inspected or changed for this inventory.

| Component | Version / provenance | License and evidence |
| --- | --- | --- |
| WebArena | Upstream benchmark; image revision not pinned here | Apache-2.0 for repository code: [upstream LICENSE](https://github.com/web-arena-x/webarena/blob/main/LICENSE). Included applications/data have separate terms. |
| WebArena-Verified | Python package **1.2.3**; installed metadata omits a license field | Apache-2.0 for project code: [v1.2.3 LICENSE](https://github.com/ServiceNow/webarena-verified/blob/v1.2.3/LICENSE). Dataset and bundled site content: see upstream. |
| WebArena / WebArena-Verified images | `am1n3e/webarena-verified-{reddit,shopping_admin,shopping,gitlab}`, default registry tags; local `osd-` aliases | Composite image contents and exact revisions: **see upstream**. The wrapper's license is not a license for every layer. [Image/project documentation](https://github.com/ServiceNow/webarena-verified/blob/v1.2.3/README.md). |
| Magento Open Source | Store/admin application in benchmark images; exact installed version unverified | OSL-3.0 for the upstream application: [LICENSE.txt](https://github.com/magento/magento2/blob/2.4-develop/LICENSE.txt). Component exceptions, assets and image modifications: **see upstream**. |
| Postmill | Forum application in benchmark images; exact installed version unverified | **See upstream** for the image's precise revision/license. The historical [Postmill source license](https://github.com/neuroradiology/Postmill/blob/master/LICENSE) is Zlib; this source mirror is not verification of the image contents. |
| BrowserGym (`browsergym-core`) | **0.14.3**, installed metadata | Apache-2.0; [upstream LICENSE](https://github.com/ServiceNow/BrowserGym/blob/main/LICENSE). |
| mitmproxy | **11.0.2**, installed metadata and license text | MIT; [versioned LICENSE](https://github.com/mitmproxy/mitmproxy/blob/v11.0.2/LICENSE). |
| Playwright (Python) | **1.44.0**, installed metadata | Apache-2.0; [versioned LICENSE](https://github.com/microsoft/playwright-python/blob/v1.44.0/LICENSE). Chromium and bundled dependencies retain separate notices. |

This is an attribution inventory, not a complete audit of transitive packages or
container layers. Consult upstream notices when redistributing third-party
material. Unknown image/application revisions are intentionally marked above
rather than inferred from the Python package version.
