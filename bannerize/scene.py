"""Blender scene assembler — run inside Blender via `--python`.

This script reads `scene.json` (produced by `bannerize export`), imports each
character's glTF gear into the scene, lays the characters out, adds a Cycles
world + HDRI + a 3-point lighting rig + a camera, and saves a .blend file.
Optionally renders to PNG.

Invoke (typically via `bannerize render`):

    blender --background --python bannerize/scene.py -- \
        --scene-json out/scene.json --out-blend out/banner.blend \
        [--hdri env.exr] [--out-image render.png] [--samples 256]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import bpy
    from mathutils import Vector
except ImportError:  # pragma: no cover - only meaningful inside Blender
    bpy = None  # type: ignore
    Vector = None  # type: ignore


# ----------------------------- argument parsing ------------------------------

def _parse_args() -> argparse.Namespace:
    # Blender stuffs script args after a literal "--"; everything before that
    # is consumed by Blender itself.
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument("--scene-json", required=True)
    p.add_argument("--out-blend", required=True)
    p.add_argument("--out-image", default=None)
    p.add_argument("--hdri", default=None)
    p.add_argument("--samples", type=int, default=128)
    p.add_argument("--resolution", default="1920x1080")
    p.add_argument("--character-spacing", type=float, default=3.0)
    return p.parse_args(argv)


# ----------------------------- scene helpers ---------------------------------

def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _set_cycles(samples: int, resolution: str):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    try:
        scene.cycles.device = "GPU"
    except AttributeError:
        pass
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    w, h = (int(x) for x in resolution.lower().split("x"))
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium High Contrast"


def _build_world(hdri_path: str | None):
    world = bpy.data.worlds.new("BannerizeWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    if hdri_path and Path(hdri_path).exists():
        tex = nt.nodes.new("ShaderNodeTexEnvironment")
        try:
            tex.image = bpy.data.images.load(hdri_path)
        except RuntimeError as exc:
            print(f"[bannerize] HDRI load failed: {exc}; falling back to sky.")
            _attach_sky(nt, bg)
        else:
            mapping = nt.nodes.new("ShaderNodeMapping")
            coord = nt.nodes.new("ShaderNodeTexCoord")
            nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
            nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            nt.links.new(tex.outputs["Color"], bg.inputs["Color"])
            bg.inputs["Strength"].default_value = 1.2
    else:
        _attach_sky(nt, bg)


def _attach_sky(nt, bg):
    """Procedural sky as a fallback when no HDRI is supplied."""
    try:
        sky = nt.nodes.new("ShaderNodeTexSky")
        sky.sky_type = "NISHITA"
        sky.sun_elevation = math.radians(15)
        sky.sun_rotation = math.radians(120)
        sky.air_density = 1.0
        sky.dust_density = 2.0
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.8
    except (AttributeError, TypeError):
        # Older Blender — solid color fallback.
        bg.inputs["Color"].default_value = (0.05, 0.07, 0.12, 1.0)
        bg.inputs["Strength"].default_value = 1.0


def _add_ground():
    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0))
    plane = bpy.context.active_object
    plane.name = "Ground"
    mat = bpy.data.materials.new("Ground_Mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.04, 0.04, 0.05, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.7
    plane.data.materials.append(mat)


def _three_point_lights(target: Vector):
    """Key + fill + rim around a target point."""
    def lamp(name, kind, loc, energy, color=(1.0, 1.0, 1.0), size=1.0):
        bpy.ops.object.light_add(type=kind, location=loc)
        o = bpy.context.active_object
        o.name = name
        o.data.energy = energy
        o.data.color = color
        if kind == "AREA":
            o.data.size = size
        constraint = o.constraints.new(type="TRACK_TO")
        empty = bpy.data.objects.new(f"{name}_target", None)
        empty.location = target
        bpy.context.collection.objects.link(empty)
        constraint.target = empty
        constraint.track_axis = "TRACK_NEGATIVE_Z"
        constraint.up_axis = "UP_Y"

    lamp("Key", "AREA", target + Vector((4, -4, 4)), energy=1200, size=3, color=(1.0, 0.95, 0.85))
    lamp("Fill", "AREA", target + Vector((-5, -3, 2.5)), energy=400, size=4, color=(0.6, 0.7, 1.0))
    lamp("Rim", "AREA", target + Vector((0, 4, 3.5)), energy=900, size=2.5, color=(1.0, 0.8, 0.7))


def _camera(target: Vector, distance: float):
    bpy.ops.object.camera_add(location=target + Vector((distance, -distance * 1.4, distance * 0.55)))
    cam = bpy.context.active_object
    cam.name = "BannerCamera"
    constraint = cam.constraints.new(type="TRACK_TO")
    empty = bpy.data.objects.new("CameraTarget", None)
    empty.location = target
    bpy.context.collection.objects.link(empty)
    constraint.target = empty
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    cam.data.lens = 70.0
    bpy.context.scene.camera = cam


# ----------------------------- import + layout -------------------------------

def _import_gltf(path: Path) -> list:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    return new


def _bounds(objs) -> tuple[Vector, Vector]:
    lo = Vector((float("inf"),) * 3)
    hi = Vector((float("-inf"),) * 3)
    for o in objs:
        if o.type != "MESH":
            continue
        for corner in o.bound_box:
            v = o.matrix_world @ Vector(corner)
            for i in range(3):
                if v[i] < lo[i]:
                    lo[i] = v[i]
                if v[i] > hi[i]:
                    hi[i] = v[i]
    return lo, hi


def _place_character(character: dict, root_dir: Path, x_offset: float):
    """Import every glTF for this character into a collection, anchor it at x_offset."""
    coll = bpy.data.collections.new(character["character_id"])
    bpy.context.scene.collection.children.link(coll)

    spawned = []
    for it in character.get("items", []):
        gltf_path = root_dir / it["gltf_path"]
        if not gltf_path.exists():
            print(f"[bannerize] missing glTF: {gltf_path}")
            continue
        new_objs = _import_gltf(gltf_path)
        for o in new_objs:
            # Re-parent into our per-character collection.
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
            spawned.append(o)

    if not spawned:
        return None

    lo, hi = _bounds(spawned)
    # Move the character so its bounding box bottom sits on the ground at x_offset.
    if not math.isinf(lo.x):
        shift = Vector((x_offset - (lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
        for o in spawned:
            o.location = o.location + shift
    return coll


def _run():
    args = _parse_args()
    scene_json = Path(args.scene_json).resolve()
    out_blend = Path(args.out_blend).resolve()
    root_dir = scene_json.parent

    data = json.loads(scene_json.read_text())
    if not isinstance(data, list):
        data = [data]

    _reset_scene()
    _set_cycles(args.samples, args.resolution)
    _build_world(args.hdri)
    _add_ground()

    n = len(data)
    spacing = args.character_spacing
    total_width = spacing * max(n - 1, 0)
    for idx, character in enumerate(data):
        x = (idx * spacing) - total_width / 2
        _place_character(character, root_dir, x)

    # Aim the lights and camera at the centre of the row, ~1.2 m up.
    centre = Vector((0, 0, 1.2))
    _three_point_lights(centre)
    cam_dist = max(4.0, total_width * 1.1 + 3.0)
    _camera(centre, cam_dist)

    out_blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out_blend))
    print(f"[bannerize] saved {out_blend}")

    if args.out_image:
        out_png = Path(args.out_image).resolve()
        bpy.context.scene.render.image_settings.file_format = "PNG"
        bpy.context.scene.render.filepath = str(out_png)
        bpy.ops.render.render(write_still=True)
        print(f"[bannerize] rendered {out_png}")


if __name__ == "__main__":
    if bpy is None:
        print("scene.py must be run inside Blender (e.g. via `bannerize render`).")
        sys.exit(2)
    _run()
