#!/usr/bin/env python3
"""3DP MCP Server — 3D printing CAD modeling with build123d."""

import json
import math
import os
import traceback

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("3dp-mcp-server")

_models: dict[str, dict] = {}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _shape_to_model_entry(shape, code: str = "") -> dict:
    """Convert a build123d shape into a model entry dict with bbox and volume."""
    bb = shape.bounding_box()
    bbox = {
        "min": [round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3)],
        "max": [round(bb.max.X, 3), round(bb.max.Y, 3), round(bb.max.Z, 3)],
        "size": [
            round(bb.max.X - bb.min.X, 3),
            round(bb.max.Y - bb.min.Y, 3),
            round(bb.max.Z - bb.min.Z, 3),
        ],
    }
    try:
        volume = round(shape.volume, 3)
    except Exception:
        volume = None
    return {"shape": shape, "code": code, "bbox": bbox, "volume": volume}


def _run_build123d_code(code: str) -> dict:
    local_ns: dict = {}
    exec_globals = {"__builtins__": __builtins__}
    exec(code, exec_globals, local_ns)

    if "result" not in local_ns:
        raise ValueError("Code must assign the final shape to a variable called `result`")

    return _shape_to_model_entry(local_ns["result"], code)


@mcp.tool()
def create_model(name: str, code: str) -> str:
    """Create a 3D model by executing build123d Python code.

    The code MUST assign the final shape to a variable called `result`.
    All build123d imports are available automatically.

    Args:
        name: A short name for the model (used for file naming)
        code: build123d Python code that creates a shape and assigns it to `result`

    Returns:
        JSON with success status, geometry info (bounding box, volume), and output paths.
    """
    try:
        if "from build123d" not in code and "import build123d" not in code:
            code = "from build123d import *\n" + code

        result = _run_build123d_code(code)
        _models[name] = result

        model_dir = os.path.join(OUTPUT_DIR, name)
        os.makedirs(model_dir, exist_ok=True)

        from build123d import export_stl, export_step
        stl_path = os.path.join(model_dir, f"{name}.stl")
        step_path = os.path.join(model_dir, f"{name}.step")
        export_stl(result["shape"], stl_path)
        export_step(result["shape"], step_path)

        return json.dumps({
            "success": True,
            "name": name,
            "bbox": result["bbox"],
            "volume": result["volume"],
            "outputs": {"stl": stl_path, "step": step_path},
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, indent=2)


@mcp.tool()
def export_model(name: str, format: str = "stl") -> str:
    """Export a model to STL, STEP, or 3MF format.

    Args:
        name: Name of a previously created model
        format: Export format - "stl", "step", or "3mf"
    """
    if name not in _models:
        return json.dumps({"success": False, "error": f"Model '{name}' not found. Available: {list(_models.keys())}"})

    model = _models[name]
    model_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(model_dir, exist_ok=True)

    fmt = format.lower().strip(".")
    out_path = os.path.join(model_dir, f"{name}.{fmt}")

    try:
        if fmt == "stl":
            from build123d import export_stl
            export_stl(model["shape"], out_path)
        elif fmt == "step":
            from build123d import export_step
            export_step(model["shape"], out_path)
        elif fmt == "3mf":
            from build123d import Mesher
            with Mesher() as mesher:
                mesher.add_shape(model["shape"])
                mesher.write(out_path)
        else:
            return json.dumps({"success": False, "error": f"Unsupported format: {fmt}"})

        return json.dumps({"success": True, "path": out_path})

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()})


@mcp.tool()
def measure_model(name: str) -> str:
    """Measure a model's geometry: bounding box, volume, surface area, and face/edge counts.

    Args:
        name: Name of a previously created model
    """
    if name not in _models:
        return json.dumps({"success": False, "error": f"Model '{name}' not found. Available: {list(_models.keys())}"})

    shape = _models[name]["shape"]
    bb = _models[name]["bbox"]
    measurements = {"name": name, "bbox": bb}

    try:
        measurements["volume_mm3"] = round(shape.volume, 3)
    except Exception:
        measurements["volume_mm3"] = None

    try:
        measurements["area_mm2"] = round(shape.area, 3)
    except Exception:
        measurements["area_mm2"] = None

    try:
        measurements["faces"] = len(shape.faces())
    except Exception:
        measurements["faces"] = None

    try:
        measurements["edges"] = len(shape.edges())
    except Exception:
        measurements["edges"] = None

    return json.dumps(measurements, indent=2)


@mcp.tool()
def analyze_printability(name: str, min_wall_mm: float = 0.8) -> str:
    """Check if a model is suitable for FDM 3D printing (e.g. Bambu Lab X1C).

    Args:
        name: Name of a previously created model
        min_wall_mm: Minimum wall thickness in mm (default 0.8)
    """
    if name not in _models:
        return json.dumps({"success": False, "error": f"Model '{name}' not found. Available: {list(_models.keys())}"})

    shape = _models[name]["shape"]
    issues = []
    checks = {}

    try:
        vol = shape.volume
        checks["volume_mm3"] = round(vol, 3)
        if vol <= 0:
            issues.append("Model has zero or negative volume")
    except Exception as e:
        issues.append(f"Cannot compute volume: {e}")

    try:
        solids = shape.solids()
        checks["solid_count"] = len(solids)
        if len(solids) == 0:
            issues.append("No solids found — not printable")
    except Exception:
        pass

    bb = _models[name]["bbox"]
    dims = bb["size"]
    checks["dimensions_mm"] = dims
    if any(d < 1.0 for d in dims):
        issues.append(f"Very small dimension ({min(dims):.1f}mm)")
    if any(d > 300 for d in dims):
        issues.append(f"Exceeds 300mm ({max(dims):.1f}mm) — may not fit bed")

    try:
        faces = shape.faces()
        checks["face_count"] = len(faces)
        if len(faces) < 4:
            issues.append("Too few faces for a valid solid")
    except Exception:
        pass

    try:
        area = shape.area
        vol = shape.volume
        if vol > 0:
            ratio = area / vol
            checks["area_volume_ratio"] = round(ratio, 4)
            if ratio > 7.5:
                issues.append(f"High area/volume ratio ({ratio:.2f}) — possible thin walls < {min_wall_mm}mm")
    except Exception:
        pass

    return json.dumps({
        "verdict": "PRINTABLE" if not issues else "REVIEW NEEDED",
        "issues": issues,
        "checks": checks,
        "printer": "Bambu Lab X1C (256x256x256mm)",
    }, indent=2)


@mcp.tool()
def list_models() -> str:
    """List all models currently loaded in this session."""
    if not _models:
        return json.dumps({"models": [], "message": "No models yet. Use create_model to make one."})

    return json.dumps({"models": [
        {"name": n, "bbox": d["bbox"], "volume": d["volume"]} for n, d in _models.items()
    ]}, indent=2)


@mcp.tool()
def get_model_code(name: str) -> str:
    """Retrieve the build123d code used to create a model.

    Args:
        name: Name of a previously created model
    """
    if name not in _models:
        return json.dumps({"success": False, "error": f"Model '{name}' not found. Available: {list(_models.keys())}"})

    return json.dumps({"name": name, "code": _models[name]["code"]})


@mcp.tool()
def transform_model(name: str, source_name: str, operations: str) -> str:
    """Scale, rotate, mirror, or translate a loaded model. Apply operations in order.

    Args:
        name: Name for the new transformed model
        source_name: Name of the source model to transform
        operations: JSON string with transform operations applied in order.
            Supported keys: "scale" (float or [x,y,z]), "rotate" ([rx,ry,rz] degrees),
            "mirror" ("XY","XZ","YZ"), "translate" ([x,y,z]).
            Can be a single dict or a list of dicts for ordered operations.
    """
    if source_name not in _models:
        return json.dumps({"success": False, "error": f"Model '{source_name}' not found. Available: {list(_models.keys())}"})

    try:
        from build123d import Mirror, Plane as B3dPlane, Pos, Rot

        shape = _models[source_name]["shape"]
        ops = json.loads(operations)
        if isinstance(ops, dict):
            ops = [ops]

        for op in ops:
            if "scale" in op:
                s = op["scale"]
                if isinstance(s, (int, float)):
                    shape = shape.scale(s)
                else:
                    shape = shape.scale(s[0], s[1], s[2])
            if "rotate" in op:
                rx, ry, rz = op["rotate"]
                shape = Rot(rx, ry, rz) * shape
            if "mirror" in op:
                plane_map = {"XY": B3dPlane.XY, "XZ": B3dPlane.XZ, "YZ": B3dPlane.YZ}
                mirror_plane = plane_map.get(op["mirror"].upper())
                if mirror_plane is None:
                    return json.dumps({"success": False, "error": f"Unknown mirror plane: {op['mirror']}. Use XY, XZ, or YZ."})
                shape = Mirror(about=mirror_plane) * shape
            if "translate" in op:
                tx, ty, tz = op["translate"]
                shape = Pos(tx, ty, tz) * shape

        entry = _shape_to_model_entry(shape, code=f"transform of {source_name}: {operations}")
        _models[name] = entry

        return json.dumps({
            "success": True,
            "name": name,
            "source": source_name,
            "bbox": entry["bbox"],
            "volume": entry["volume"],
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def import_model(name: str, file_path: str) -> str:
    """Import an STL or STEP file from disk into the server as a loaded model.

    Args:
        name: Name for the imported model
        file_path: Absolute path to the STL or STEP file
    """
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".stl":
            from build123d import import_stl
            shape = import_stl(file_path)
        elif ext in (".step", ".stp"):
            from build123d import import_step
            shape = import_step(file_path)
        else:
            return json.dumps({"success": False, "error": f"Unsupported file type: {ext}. Use .stl, .step, or .stp."})

        entry = _shape_to_model_entry(shape, code=f"imported from {file_path}")
        _models[name] = entry

        return json.dumps({
            "success": True,
            "name": name,
            "file": file_path,
            "bbox": entry["bbox"],
            "volume": entry["volume"],
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def estimate_print(name: str, infill_percent: float = 15.0, layer_height: float = 0.2, material: str = "PLA") -> str:
    """Estimate filament usage, weight, and cost for printing a model.

    Args:
        name: Name of a previously created model
        infill_percent: Infill percentage (default 15)
        layer_height: Layer height in mm (default 0.2)
        material: Filament material - PLA, PETG, ABS, TPU, or ASA (default PLA)
    """
    if name not in _models:
        return json.dumps({"success": False, "error": f"Model '{name}' not found. Available: {list(_models.keys())}"})

    try:
        shape = _models[name]["shape"]
        density_map = {
            "PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "TPU": 1.21, "ASA": 1.07,
        }
        mat = material.upper()
        if mat not in density_map:
            return json.dumps({"success": False, "error": f"Unknown material: {material}. Supported: {list(density_map.keys())}"})

        density = density_map[mat]  # g/cm^3
        filament_diameter = 1.75  # mm
        cost_per_kg = 20.0  # USD

        total_volume_mm3 = shape.volume  # mm^3
        surface_area_mm2 = shape.area  # mm^2

        wall_thickness = 0.8  # mm per perimeter
        num_perimeters = 2
        shell_volume_mm3 = surface_area_mm2 * wall_thickness * num_perimeters

        interior_volume_mm3 = max(0, total_volume_mm3 - shell_volume_mm3)
        infill_volume_mm3 = interior_volume_mm3 * (infill_percent / 100.0)

        used_volume_mm3 = shell_volume_mm3 + infill_volume_mm3
        used_volume_cm3 = used_volume_mm3 / 1000.0

        weight_g = used_volume_cm3 * density

        filament_cross_section = math.pi * (filament_diameter / 2.0) ** 2  # mm^2
        filament_length_mm = used_volume_mm3 / filament_cross_section
        filament_length_m = filament_length_mm / 1000.0

        cost = (weight_g / 1000.0) * cost_per_kg

        # Rough time estimate: based on volume and layer height
        layers = _models[name]["bbox"]["size"][2] / layer_height
        # Very rough: ~2 seconds per layer + volume-based component
        est_minutes = (layers * 2.0 + used_volume_mm3 / 500.0) / 60.0

        return json.dumps({
            "success": True,
            "name": name,
            "material": mat,
            "infill_percent": infill_percent,
            "layer_height_mm": layer_height,
            "model_volume_mm3": round(total_volume_mm3, 1),
            "shell_volume_mm3": round(shell_volume_mm3, 1),
            "infill_volume_mm3": round(infill_volume_mm3, 1),
            "total_filament_volume_mm3": round(used_volume_mm3, 1),
            "weight_g": round(weight_g, 1),
            "filament_length_m": round(filament_length_m, 2),
            "estimated_cost_usd": round(cost, 2),
            "estimated_time_min": round(est_minutes, 0),
            "density_g_cm3": density,
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def combine_models(name: str, model_a: str, model_b: str, operation: str = "union") -> str:
    """Boolean combine two loaded models: union, subtract, or intersect.

    Args:
        name: Name for the resulting combined model
        model_a: Name of the first model
        model_b: Name of the second model
        operation: Boolean operation - "union", "subtract", or "intersect"
    """
    for m in (model_a, model_b):
        if m not in _models:
            return json.dumps({"success": False, "error": f"Model '{m}' not found. Available: {list(_models.keys())}"})

    try:
        a = _models[model_a]["shape"]
        b = _models[model_b]["shape"]

        op = operation.lower()
        if op == "union":
            result = a + b
        elif op == "subtract":
            result = a - b
        elif op == "intersect":
            result = a & b
        else:
            return json.dumps({"success": False, "error": f"Unknown operation: {operation}. Use union, subtract, or intersect."})

        entry = _shape_to_model_entry(result, code=f"{model_a} {op} {model_b}")
        _models[name] = entry

        return json.dumps({
            "success": True,
            "name": name,
            "operation": op,
            "model_a": model_a,
            "model_b": model_b,
            "bbox": entry["bbox"],
            "volume": entry["volume"],
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def shell_model(name: str, source_name: str, thickness: float = 2.0, open_faces: str = "[]") -> str:
    """Hollow out a model, optionally leaving faces open.

    Args:
        name: Name for the new shelled model
        source_name: Name of the source model to hollow
        thickness: Wall thickness in mm (default 2.0)
        open_faces: JSON list of face directions to leave open, e.g. '["top"]' or '["bottom"]'.
            Supported: "top", "bottom". Default is no open faces.
    """
    if source_name not in _models:
        return json.dumps({"success": False, "error": f"Model '{source_name}' not found. Available: {list(_models.keys())}"})

    try:
        from build123d import Axis

        shape = _models[source_name]["shape"]
        faces_to_open = json.loads(open_faces) if isinstance(open_faces, str) else open_faces

        openings = []
        if faces_to_open:
            all_faces = shape.faces()
            for face_dir in faces_to_open:
                fd = face_dir.lower()
                if fd == "top":
                    # Find face with highest Z center
                    top_face = max(all_faces, key=lambda f: f.center().Z)
                    openings.append(top_face)
                elif fd == "bottom":
                    bottom_face = min(all_faces, key=lambda f: f.center().Z)
                    openings.append(bottom_face)
                elif fd == "front":
                    front_face = max(all_faces, key=lambda f: f.center().Y)
                    openings.append(front_face)
                elif fd == "back":
                    back_face = min(all_faces, key=lambda f: f.center().Y)
                    openings.append(back_face)
                elif fd == "left":
                    left_face = min(all_faces, key=lambda f: f.center().X)
                    openings.append(left_face)
                elif fd == "right":
                    right_face = max(all_faces, key=lambda f: f.center().X)
                    openings.append(right_face)

        result = shape.shell(openings=openings, thickness=-thickness)

        entry = _shape_to_model_entry(result, code=f"shell of {source_name}, thickness={thickness}")
        _models[name] = entry

        return json.dumps({
            "success": True,
            "name": name,
            "source": source_name,
            "thickness_mm": thickness,
            "open_faces": faces_to_open,
            "bbox": entry["bbox"],
            "volume": entry["volume"],
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def split_model(name: str, source_name: str, plane: str = "XY", keep: str = "both") -> str:
    """Split a model along a plane.

    Args:
        name: Base name for the resulting model(s)
        source_name: Name of the source model to split
        plane: Split plane - "XY", "XZ", "YZ", or JSON like '{"axis": "Z", "offset": 10.5}'
        keep: Which half to keep - "above", "below", or "both" (default "both").
            If "both", saves as name_above and name_below.
    """
    if source_name not in _models:
        return json.dumps({"success": False, "error": f"Model '{source_name}' not found. Available: {list(_models.keys())}"})

    try:
        from build123d import Axis, Box, Plane as B3dPlane, Pos

        shape = _models[source_name]["shape"]
        bb = shape.bounding_box()

        # Parse plane specification
        offset = 0.0
        if plane.startswith("{"):
            plane_spec = json.loads(plane)
            axis = plane_spec.get("axis", "Z").upper()
            offset = plane_spec.get("offset", 0.0)
        else:
            # Map plane name to axis normal
            plane_axis_map = {"XY": "Z", "XZ": "Y", "YZ": "X"}
            axis = plane_axis_map.get(plane.upper())
            if axis is None:
                return json.dumps({"success": False, "error": f"Unknown plane: {plane}. Use XY, XZ, YZ."})

        # Create a large cutting box for bisecting
        size = max(bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z) * 4 + 200
        half = size / 2

        if axis == "Z":
            # "above" = positive Z from offset, "below" = negative Z from offset
            above_box = Pos(0, 0, offset + half) * Box(size, size, size)
            below_box = Pos(0, 0, offset - half) * Box(size, size, size)
        elif axis == "Y":
            above_box = Pos(0, offset + half, 0) * Box(size, size, size)
            below_box = Pos(0, offset - half, 0) * Box(size, size, size)
        elif axis == "X":
            above_box = Pos(offset + half, 0, 0) * Box(size, size, size)
            below_box = Pos(offset - half, 0, 0) * Box(size, size, size)

        results = {}
        if keep in ("above", "both"):
            above_shape = shape & above_box
            above_entry = _shape_to_model_entry(above_shape, code=f"split {source_name} above {plane}")
            result_name = f"{name}_above" if keep == "both" else name
            _models[result_name] = above_entry
            results[result_name] = {"bbox": above_entry["bbox"], "volume": above_entry["volume"]}

        if keep in ("below", "both"):
            below_shape = shape & below_box
            below_entry = _shape_to_model_entry(below_shape, code=f"split {source_name} below {plane}")
            result_name = f"{name}_below" if keep == "both" else name
            _models[result_name] = below_entry
            results[result_name] = {"bbox": below_entry["bbox"], "volume": below_entry["volume"]}

        return json.dumps({
            "success": True,
            "source": source_name,
            "plane": plane,
            "keep": keep,
            "results": results,
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def section_view(name: str, source_name: str, plane: str = "XY", offset: float = 0.0) -> str:
    """Generate a 2D cross-section of a model and export as SVG.

    Args:
        name: Name for the cross-section result
        source_name: Name of the source model to section
        plane: Section plane - "XY", "XZ", or "YZ" (default "XY")
        offset: Position along the plane normal axis (default 0.0)
    """
    if source_name not in _models:
        return json.dumps({"success": False, "error": f"Model '{source_name}' not found. Available: {list(_models.keys())}"})

    try:
        from build123d import Plane as B3dPlane, Vector, ExportSVG

        shape = _models[source_name]["shape"]

        plane_map = {
            "XY": B3dPlane.XY,
            "XZ": B3dPlane.XZ,
            "YZ": B3dPlane.YZ,
        }
        section_plane = plane_map.get(plane.upper())
        if section_plane is None:
            return json.dumps({"success": False, "error": f"Unknown plane: {plane}. Use XY, XZ, or YZ."})

        # Apply offset
        if offset != 0.0:
            section_plane = section_plane.offset(offset)

        # Create cross-section
        section = shape.section(section_plane)

        # Store the section as a model entry
        entry = _shape_to_model_entry(section, code=f"section of {source_name} at {plane} offset={offset}")
        _models[name] = entry

        # Export SVG
        model_dir = os.path.join(OUTPUT_DIR, name)
        os.makedirs(model_dir, exist_ok=True)
        svg_path = os.path.join(model_dir, f"{name}.svg")

        exporter = ExportSVG(scale=2.0)
        exporter.add_layer("section")
        exporter.add_shape(section, layer="section")
        exporter.write(svg_path)

        return json.dumps({
            "success": True,
            "name": name,
            "source": source_name,
            "plane": plane,
            "offset": offset,
            "svg_path": svg_path,
            "bbox": entry["bbox"],
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


@mcp.tool()
def export_drawing(name: str, views: str = '["front", "top", "right"]', page_size: str = "A4") -> str:
    """Generate a 2D technical drawing as SVG with multiple view projections.

    Args:
        name: Name of a previously created model
        views: JSON list of view directions, e.g. '["front", "top", "right", "iso"]'
        page_size: Page size - "A4" or "A3" (default "A4")
    """
    if name not in _models:
        return json.dumps({"success": False, "error": f"Model '{name}' not found. Available: {list(_models.keys())}"})

    try:
        from build123d import ExportSVG, Vector

        shape = _models[name]["shape"]
        view_list = json.loads(views) if isinstance(views, str) else views

        model_dir = os.path.join(OUTPUT_DIR, name)
        os.makedirs(model_dir, exist_ok=True)
        svg_path = os.path.join(model_dir, f"{name}_drawing.svg")

        # Map view names to direction vectors (camera looks FROM this direction)
        view_directions = {
            "front": Vector(0, -1, 0),
            "back": Vector(0, 1, 0),
            "right": Vector(1, 0, 0),
            "left": Vector(-1, 0, 0),
            "top": Vector(0, 0, 1),
            "bottom": Vector(0, 0, -1),
            "iso": Vector(1, -1, 1),
        }

        exporter = ExportSVG(scale=1.0)

        for i, view_name in enumerate(view_list):
            vn = view_name.lower()
            direction = view_directions.get(vn)
            if direction is None:
                return json.dumps({"success": False, "error": f"Unknown view: {view_name}. Supported: {list(view_directions.keys())}"})

            layer_name = f"view_{vn}"
            exporter.add_layer(layer_name)
            exporter.add_shape(shape, layer=layer_name, line_type=ExportSVG.LineType.VISIBLE,
                               view_port_origin=direction)

        exporter.write(svg_path)

        return json.dumps({
            "success": True,
            "name": name,
            "views": view_list,
            "svg_path": svg_path,
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
