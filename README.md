# bannerize

Pull your Destiny 2 guardians from Bungie's Platform API and export a
Blender‑ready Cycles scene with lighting, HDRI, and a camera, ready to render.

```
bungie.net API ──▶ profile + equipment + sockets
                                │
                                ▼
            mobile gear‑assets SQLite  ──▶ TGXM filenames on the CDN
                                │
                                ▼
                geometry/ + textures/.tgxm  ──▶ decode (positions × scale + offset)
                                │
                                ▼
                          .gltf + .bin + textures
                                │
                                ▼
                  Blender (Cycles + HDRI + 3‑point lights)
```

## Setup

### 1. Register a Bungie app

Go to <https://www.bungie.net/en/Application> and create an application:

| Field | Value |
|---|---|
| OAuth Client Type | **Confidential** (so you get a refresh token) |
| Redirect URL | `http://localhost:7777/callback` |
| Scope | tick **Read your Destiny vault and inventory** (`ReadDestinyInventoryAndVault`) |

Bungie hands you an **API key**, **client ID**, and **client secret**.

### 2. Install bannerize

```bash
pip install -e .
```

Python 3.10+. Pure-Python; only deps are `requests` and (for users
who prefer it) `pygltflib`. Blender is only needed for the final
render step.

### 3. Configure credentials

Either env vars:

```bash
export BUNGIE_API_KEY=…
export BUNGIE_CLIENT_ID=…
export BUNGIE_CLIENT_SECRET=…
export BUNGIE_REDIRECT_URI=http://localhost:7777/callback
```

…or `~/.config/bannerize/config.json`:

```json
{
  "api_key": "…",
  "client_id": "…",
  "client_secret": "…",
  "redirect_uri": "http://localhost:7777/callback"
}
```

### 4. Authorize and export

```bash
bannerize auth        # opens a browser; saves tokens.json
bannerize export      # writes out/<characterId>/*.gltf and out/scene.json
bannerize render --hdri studio.exr --out-image banner.png --samples 256
```

`render` shells out to Blender (`blender --background`). Pass `--blender
/path/to/blender` if it isn't on `PATH`.

## What gets exported

For every character on the account:

* `out/<characterId>/character.json` — class, race, gender, light, item list
* `out/<characterId>/<slot>_<itemHash>.gltf` (+ `.bin`, + diffuse PNG/JPG)
  per equipped armor/weapon
* `out/scene.json` — the assembly hint consumed by `scene.py`

`scene.py` lays the characters out in a row, drops them on a dark ground
plane, builds a Cycles world (HDRI if given, Nishita sky otherwise), and
adds an area‑light **key / fill / rim** rig with a 70 mm camera.

## Architecture

| Module | Responsibility |
|---|---|
| `bannerize.config` | Credentials + token persistence (`~/.config/bannerize/`) |
| `bannerize.auth` | OAuth 2.0 authorization‑code flow with a loopback listener |
| `bannerize.api` | Minimal Bungie Platform client (`GetManifest`, `GetMembershipsForCurrentUser`, `GetProfile`) |
| `bannerize.manifest` | Per‑table JSON manifest cache (`jsonWorldComponentContentPaths`) |
| `bannerize.gear_assets` | Mobile gear‑assets SQLite + CDN URL helpers |
| `bannerize.sockets` | Identify the equipped shader / ornament on an item |
| `bannerize.tgxm` | TGXM v1/v2 container parser (Bungie's own `spasm_tgx_bin_loader.js` format) |
| `bannerize.geometry` | `render_metadata.js` parser + vertex/index decode (position = a_pos × scale + offset) |
| `bannerize.gltf_export` | Hand‑rolled glTF 2.0 writer |
| `bannerize.pipeline` | Orchestrates profile → glTFs on disk |
| `bannerize.scene` | Blender bpy script: imports glTFs, builds the Cycles scene |
| `bannerize.cli` | argparse entry point (`auth`, `export`, `geometry`, `render`) |

## Honest limitations

* **Materials are diffuse‑only.** Bungie's gearstack/dye system needs
  shader‑graph plumbing we haven't wired up yet. The first diffuse plate
  in each item's texture TGXM is set as `baseColorTexture`. Dye colours
  and the per‑slot `gear_dye_change_color_index` are preserved as `extras`
  on each glTF primitive so a future shader pass can apply them.
* **Stage 0, LOD 0 only.** Decals (stage 1) and transparents (stage 7)
  are skipped. Highest LOD only.
* **No skeletons / no animation.** TGXM does carry a skin buffer (bone
  indices + weights), but we currently emit static meshes. Posing in
  Blender is straightforward; rigged poses are not.
* **In‑game shader is not fully public.** The web shader source
  (`spasm_webgl_gear_shader.js`) only uses gearstack R and G; B/A use in
  the in‑engine shader is not publicly documented.

## Bungie Terms of Use

This tool downloads Bungie's manifest and gear assets directly from
`bungie.net` at runtime. Don't redistribute or bundle the downloaded
geometry, textures, or rendered models — they remain Bungie's property
under the [Bungie.net Terms of Use](https://www.bungie.net/7/en/legal/terms).
Personal / non‑commercial use unless you have written permission from
Bungie. Be polite with rate limits (this tool only reads, but throttle
yourself if you script it).

## Acknowledgements

The TGXM container format and the render‑mesh schema were originally
reverse‑engineered by the community. The most authoritative references
are:

* Bungie's own `spasm_tgx_bin_loader.js` / `spasm_render_mesh.js` /
  `spasm_webgl_gear_shader.js`, mirrored verbatim inside
  [`lowlines/destiny-tgx-loader`](https://github.com/lowlines/destiny-tgx-loader).
* [`bepo13/destiny-stl-generator`](https://github.com/bepo13/destiny-stl-generator)
  (MIT) — pure‑Python TGXM reader powering destinystlgenerator.com.
* [`TiredHobgoblin/Destiny-Collada-Generator`](https://github.com/TiredHobgoblin/Destiny-Collada-Generator)
  — most complete vertex‑stream / dye‑slot decoder.
* [Bungie‑net/api wiki — 3D Content Documentation](https://github.com/Bungie-net/api/wiki/3D-Content-Documentation).

## Licence

MIT for the code in this repo. Game assets fetched at runtime remain
the property of Bungie.
