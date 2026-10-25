# ASTRAL CHAIN (`01007300020FA000`)

Update 1.0.1 (v65536) over the base game. PlatinumGames' engine, SDK 6.4.0, NVN, Wwise audio through `AudioOut`; modules `main` and `sdk`. The Mac reaches the opening motorcycle tutorial at 30 fps. An unsigned IPA builds; iPhone testing is pending.

## What it needs

| Setting | Where | Why |
|---|---|---|
| `mem_reserve 0xf0000000`, `mem_alias 0x10000000`, `mem_stack 0x10000000`, `app_pool 0xCD500000` | [`game.tsv`](game.tsv) | 3 GB heap region; the first Mac boot requests 2.96 GB, with the game's 191 MB BSS counted as used memory |
| `sys_resource 0x0` | `game.tsv` | the NPDM declares no system resource; no virtual address memory |
| `pad_layout full` | `game.tsv` | movement and camera use both sticks |

## Files and shaders

- 82 PKZ archives, version `0x10000`, holding 8,556 Zstandard-compressed entries, alongside Bink 2 movies (`.bk2`) and Wwise banks and streams. The PKZ directory and name-table layout matches version `0x20000`; every entry's bounds were checked and one entry per archive was decoded before extending the reader.
- `root.pkz` contains PlatinumGames DAT shader archives. The full split finds 1,348 distinct shaders: 540 vertex, 738 pixel, 70 compute. Single-stage programs produce 1,620 cover pairs. All 1,348 compile for Mac and iOS, with zero undecoded bindings and zero unfillable driver-bank reads.
- GPU code versions in the root shader archives include 1.9, 1.12 and 1.15, despite the linked SDK being 6.4.0. The game sets upper-left window origin, near-zero depth, a 1600×900 swap chain and present interval 2.

## Services and SDK

- **Resolved: startup fails, then cleanup reads null at `main+0x9c0d1c`.** `main+0x40d6c0` loads `hwfillcolor`; its vertex shader is version 1.15. The shader loader compares byte 7 with the driver's newest minor at `main+0x9e97d0` and rejects the old answer 1.14. A probe at `main+0x9dcf28` confirms failure before the subsequent cleanup crash. The host now advertises the translator's supported GPU-code range independently of the linked SDK. No game-code cut is needed.
- [Issue #170](https://github.com/seedds/nxnative/issues/170) independently reports the same initialization crash and PKZ version. Its shader-map generic-type warning was also confirmed: `g_shmap` holds strings, and its declaration now agrees. Its translator source-hash failure was not reproduced; the pinned translator built all shaders without rebuilding the translator.

## Status on the Mac

- ZL+ZR prompt, logos, opening real-time cinematic, character creation (including software-keyboard naming), save creation/reload and the motorcycle tutorial render on an M4 Pro. The tutorial's warm frame median is 33.34 ms; first-use pipeline compilation can produce longer frames.
- Wwise opens a stereo `AudioOut`; the 15-minute `play03` run reaches 84,000 device callbacks with zero reported underruns. Resident memory grows from 1.44 GB at the title to 2.57 GB on entering the tutorial; Metal allocated memory is about 3.02 GB there.
- The first boot rendered in a Mac window. A later windowed run stalled in the display link; the sustained measurements and screenshots use `DM_WINDOW` unset and `DM_PRESENT_LINK=0`. They measure offscreen native rendering, not on-screen presentation quality.
- **Current:** entering the motorcycle scene reports one cube/2D texture mismatch, handled by the existing [zero-cube fallback](../../../docs/reference/rendering.md#a-2d-texture-in-a-cube-slot-binds-the-zero-cube). The scene renders, but that binding has not been traced to establish whether it is an intentional placeholder.
- Work folder: `/Volumes/T7/nsp/ASTRAL CHAIN/01007300020FA000-work`. `run/boot.py <log-name> <seconds> [KEY=VALUE ...]` launches a bounded run, with its control file at `run/<log-name>.ctl`, logs and saves beside it. The build artifacts and extracted data are in the adjacent `01007300020FA000_build`.

## Frame rate

**Current: 30 fps; 60 fps is not enabled in the profile or IPA.** The `fps60` experiment reaches 60.0 fps at the title on the M4 Pro with `DM_PRESENT_INTERVAL=display` and the four [FPSLocker timing cuts](https://github.com/masagrator/FPSLocker-Warehouse/blob/v4/SaltySD/plugins/FPSLocker/patches/01007300020FA000/4B159F0F7A360669.yaml). Those exact sites were verified on main build `4b159f0f7a360669738390ee8657b4f0`: `0x91dc94`, `0x91dca0` and `0x91dcac` lower the elapsed-time clamp from 1 to 0.5 in 30 Hz units; `0x91dc10` makes cutscenes use elapsed time. The experiment is preserved as `fpslocker-experiment.py` in the work folder and was removed before the final rewrite/pack.

The clamp change alone is insufficient: `main+0x48be14` truncates the floating step to an integer before incrementing a counter toward 30, so half-frame steps can stop it; `main+0x57e7e0` similarly truncates another counter. Other action/input windows count frames directly. [AstralChain-Dynamic-FPS](https://github.com/yogesh1239/AstralChain-Dynamic-FPS) provides an MIT-licensed, experimental larger patch for these consumers and camera smoothing, but reports remaining cutscene audio drift and incomplete timer coverage. Its additional hooks were surveyed locally; it was not integrated or runtime-validated here. A production 60 fps profile needs those semantics verified, including combat input windows and audio/video sync, rather than only a higher present rate.

## Found on this game

- [PKZ version 1 archives](../../../docs/reference/shaders.md#pkz-version-1-archives).
- [Newest GPU code version reported by the driver](../../../docs/reference/nvn-objects.md#newest-gpu-code-version-reported-by-the-driver): precompiled shader versions do not follow the linked SDK.

## Open

- Validate the unsigned IPA on an iPhone, including memory pressure and touch controls.
- Trace the tutorial's cube placeholder and validate visible effects against the original game.
- Complete and validate the 60 fps timing work above; the shipped profile retains the game's own interval and timing.
- Later levels and Bink movies are untested.
