"""
Intertwined Strands - Geometry Nodes builder for Blender 4.2+ / 5.x

Builds a Geometry Nodes group that turns guide curves (or an edge-only mesh
skeleton) into several twisted, noisy tubes wrapped around each guide, like a
braided tree trunk, twisted roots or vines.

How to use:
  1. Open the Scripting workspace, New text, paste this file, press Run Script.
  2. If the active object is a curve, the modifier is added to it.
     Otherwise a demo tree guide curve ("IntertwinedTree") is created.
  3. Tweak the inputs in the modifier panel. Scale the guide curve's point
     radius (Alt+S in Edit Mode) to make the trunk fatter and the tips thinner.

Running it again rebuilds the node group in place (existing modifiers keep
using it, but their inputs go back to the defaults).
"""

import bpy

GROUP_NAME = "Intertwined Strands"
MOD_NAME = "Intertwined Strands"
TAU = 6.283185307179586


# ---------------------------------------------------------------- helpers

def find_socket(sockets, key):
    """Socket by index or name, preferring the enabled one (some nodes have
    several sockets with the same name for different data types)."""
    if isinstance(key, int):
        return sockets[key]
    fallback = None
    for s in sockets:
        if s.name == key or s.identifier == key:
            if getattr(s, "enabled", True):
                return s
            fallback = fallback or s
    if fallback is None:
        raise KeyError(f"socket '{key}' not found")
    return fallback


def set_enum(node, prop, value, socket_name=None, socket_value=None):
    """Older versions use a node property, newer ones a menu socket."""
    if hasattr(node, prop):
        try:
            setattr(node, prop, value)
            return
        except (TypeError, AttributeError):
            pass
    if socket_name:
        find_socket(node.inputs, socket_name).default_value = socket_value


class Builder:
    def __init__(self, ng):
        self.nodes = ng.nodes
        self.links = ng.links

    def node(self, idname, loc, label=None, **props):
        n = self.nodes.new(idname)
        n.location = loc
        if label:
            n.label = label
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def feed(self, node, key, value):
        sock = find_socket(node.inputs, key)
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, sock)
        else:
            sock.default_value = value

    def math(self, op, a, b, loc, label=None):
        n = self.node("ShaderNodeMath", loc, label, operation=op)
        self.feed(n, 0, a)
        if b is not None:
            self.feed(n, 1, b)
        return n.outputs[0]

    def vmath(self, op, a, b, loc, label=None):
        n = self.node("ShaderNodeVectorMath", loc, label, operation=op)
        self.feed(n, 0, a)
        if b is not None:
            self.feed(n, "Scale" if op == "SCALE" else 1, b)
        return find_socket(n.outputs, "Vector")

    def float_curve(self, value, points, loc, label):
        n = self.node("ShaderNodeFloatCurve", loc, label)
        self.feed(n, "Value", value)
        curve = n.mapping.curves[0]
        curve.points[0].location = points[0]
        curve.points[1].location = points[-1]
        for x, y in points[1:-1]:
            curve.points.new(x, y)
        n.mapping.update()
        return find_socket(n.outputs, "Value")

    def random_float(self, lo, hi, id_, seed, loc, label):
        n = self.node("FunctionNodeRandomValue", loc, label, data_type="FLOAT")
        self.feed(n, "Min", lo)
        self.feed(n, "Max", hi)
        self.feed(n, "ID", id_)
        self.feed(n, "Seed", seed)
        return find_socket(n.outputs, "Value")


def out(node, name):
    return find_socket(node.outputs, name)


# ---------------------------------------------------------------- node group

INPUTS = [
    # name, socket type, default, min, max, description
    ("Strand Count", "NodeSocketInt", 5, 1, 32, "Strands wrapped around each guide curve"),
    ("Twists per Meter", "NodeSocketFloat", 0.5, -20.0, 20.0, "Full turns per meter of guide (negative = other direction)"),
    ("Spread", "NodeSocketFloat", 0.08, 0.0, 10.0, "Distance of the strands from the guide, times guide radius"),
    ("Spread Random", "NodeSocketFloat", 0.3, 0.0, 1.0, "Per-strand variation of the spread"),
    ("Strand Radius", "NodeSocketFloat", 0.06, 0.0, 10.0, "Tube radius, times guide radius"),
    ("Radius Random", "NodeSocketFloat", 0.4, 0.0, 1.0, "Per-strand variation of the tube radius"),
    ("Noise Strength", "NodeSocketFloat", 0.05, 0.0, 10.0, "Organic wobble of each strand"),
    ("Noise Scale", "NodeSocketFloat", 1.5, 0.0, 100.0, "Frequency of the wobble"),
    ("Resample Length", "NodeSocketFloat", 0.03, 0.001, 10.0, "Point spacing along the strands"),
    ("Profile Resolution", "NodeSocketInt", 8, 3, 64, "Sides of each tube"),
    ("Seed", "NodeSocketInt", 0, -10000, 10000, "Random seed"),
    ("Fuse Strands", "NodeSocketBool", False, None, None, "Melt strands into one mesh via volume remesh"),
    ("Fuse Voxel Size", "NodeSocketFloat", 0.012, 0.002, 1.0, "Detail of the fused mesh (smaller = heavier)"),
]


def build_group():
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    if hasattr(ng, "is_modifier"):
        ng.is_modifier = True

    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for name, stype, default, lo, hi, desc in INPUTS:
        s = ng.interface.new_socket(name, description=desc, in_out="INPUT", socket_type=stype)
        s.default_value = default
        if lo is not None:
            s.min_value, s.max_value = lo, hi
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    b = Builder(ng)
    gi = b.node("NodeGroupInput", (-2000, 0))
    go = b.node("NodeGroupOutput", (2000, 0))

    def I(name):
        return out(gi, name)

    # -- 1. Guides: curves pass through, edge-only meshes become curves
    sep = b.node("GeometryNodeSeparateComponents", (-1800, 300))
    b.feed(sep, 0, I("Geometry"))
    m2c = b.node("GeometryNodeMeshToCurve", (-1600, 200))
    b.feed(m2c, "Mesh", out(sep, "Mesh"))
    m2c_r = b.node("GeometryNodeSetCurveRadius", (-1400, 200), "Mesh guides radius = 1")
    b.feed(m2c_r, "Curve", out(m2c, "Curve"))
    b.feed(m2c_r, "Radius", 1.0)
    join = b.node("GeometryNodeJoinGeometry", (-1200, 300))
    b.feed(join, 0, out(sep, "Curve"))
    b.feed(join, 0, out(m2c_r, "Curve"))

    res = b.node("GeometryNodeResampleCurve", (-1000, 300))
    set_enum(res, "mode", "LENGTH", "Mode", "Length")
    b.feed(res, "Curve", out(join, "Geometry"))
    b.feed(res, "Length", I("Resample Length"))

    # -- 2. One copy of every guide per strand
    dup = b.node("GeometryNodeDuplicateElements", (-800, 300))
    try:
        dup.domain = "CURVE"
    except TypeError:
        dup.domain = "SPLINE"
    b.feed(dup, "Geometry", out(res, "Curve"))
    b.feed(dup, "Amount", I("Strand Count"))
    dup_index = out(dup, "Duplicate Index")

    # Per-point inputs, read from the guide copies
    sp = b.node("GeometryNodeSplineParameter", (-1000, -100))
    cop = b.node("GeometryNodeCurveOfPoint", (-1000, -300))
    strand_id = out(cop, "Curve Index")
    guide_r = out(b.node("GeometryNodeInputRadius", (-1000, -450)), "Radius")
    tangent = out(b.node("GeometryNodeInputTangent", (-1000, -550)), "Tangent")
    normal = out(b.node("GeometryNodeInputNormal", (-1000, -650)), "Normal")
    position = out(b.node("GeometryNodeInputPosition", (-1000, -750)), "Position")

    # -- 3. Angle = length along guide * twists * 2pi + strand slot * 2pi / count
    a_twist = b.math("MULTIPLY", out(sp, "Length"), I("Twists per Meter"), (-800, -100))
    a_twist = b.math("MULTIPLY", a_twist, TAU, (-600, -100), "x 2pi")
    a_slot = b.math("DIVIDE", dup_index, I("Strand Count"), (-800, 0), "Strand slot")
    a_slot = b.math("MULTIPLY", a_slot, TAU, (-600, 0), "x 2pi")
    angle = b.math("ADD", a_twist, a_slot, (-400, -50), "Angle")
    cos_a = b.math("COSINE", angle, None, (-200, 0))
    sin_a = b.math("SINE", angle, None, (-200, -150))

    # Direction around the guide: Normal * cos + Binormal * sin
    binormal = b.vmath("CROSS_PRODUCT", tangent, normal, (-800, -550))
    binormal = b.vmath("NORMALIZE", binormal, None, (-600, -550), "Binormal")
    ring = b.vmath("ADD",
                   b.vmath("SCALE", normal, cos_a, (0, -400)),
                   b.vmath("SCALE", binormal, sin_a, (0, -550)),
                   (200, -450), "Ring direction")

    # Distance from guide: Spread * profile * random * guide radius
    spread_profile = b.float_curve(out(sp, "Factor"),
                                   [(0.0, 1.0), (0.6, 0.75), (1.0, 0.2)],
                                   (-600, -250), "Spread along guide")
    sr_lo = b.math("SUBTRACT", 1.0, I("Spread Random"), (-400, -800))
    sr_hi = b.math("ADD", 1.0, I("Spread Random"), (-400, -950))
    spread_rand = b.random_float(sr_lo, sr_hi, strand_id, I("Seed"), (-200, -850), "Random spread")
    dist = b.math("MULTIPLY", I("Spread"), spread_profile, (-200, -300))
    dist = b.math("MULTIPLY", dist, spread_rand, (0, -250))
    dist = b.math("MULTIPLY", dist, guide_r, (200, -250), "Distance")
    ring_offset = b.vmath("SCALE", ring, dist, (400, -350))

    # Organic wobble: 4D noise, W = strand id so every strand differs
    noise = b.node("ShaderNodeTexNoise", (0, -1050), "Wobble", noise_dimensions="4D")
    b.feed(noise, "Vector", position)
    b.feed(noise, "W", b.math("MULTIPLY", strand_id, 1.7, (-200, -1100)))
    b.feed(noise, "Scale", I("Noise Scale"))
    b.feed(noise, "Detail", 1.0)
    wobble = b.vmath("SUBTRACT", out(noise, "Color"), None, (200, -1000))
    find_socket(wobble.node.inputs, 1).default_value = (0.5, 0.5, 0.5)
    wobble_amt = b.math("MULTIPLY", I("Noise Strength"), guide_r, (200, -1200))
    wobble = b.vmath("SCALE", wobble, wobble_amt, (400, -1000))

    offset = b.vmath("ADD", ring_offset, wobble, (600, -500), "Offset")
    setpos = b.node("GeometryNodeSetPosition", (800, 300))
    b.feed(setpos, "Geometry", out(dup, "Geometry"))
    b.feed(setpos, "Offset", offset)

    # -- 4. Tube radius: Strand Radius * taper * random * guide radius
    taper = b.float_curve(out(sp, "Factor"), [(0.0, 1.0), (1.0, 0.35)],
                          (600, -150), "Taper along guide")
    rr_lo = b.math("SUBTRACT", 1.0, I("Radius Random"), (600, -1300))
    seed2 = b.math("ADD", I("Seed"), 17.0, (600, -1450))
    r_rand = b.random_float(rr_lo, 1.0, strand_id, seed2, (800, -1300), "Random radius")
    rad = b.math("MULTIPLY", I("Strand Radius"), taper, (800, -100))
    rad = b.math("MULTIPLY", rad, r_rand, (1000, -100))
    rad = b.math("MULTIPLY", rad, guide_r, (1200, -100), "Tube radius")
    setrad = b.node("GeometryNodeSetCurveRadius", (1000, 300))
    b.feed(setrad, "Curve", out(setpos, "Geometry"))
    b.feed(setrad, "Radius", rad)

    # -- 5. Tubes, optional volume fuse, smooth shading
    circle = b.node("GeometryNodeCurvePrimitiveCircle", (1000, 500), "Profile")
    b.feed(circle, "Resolution", I("Profile Resolution"))
    b.feed(circle, "Radius", 1.0)
    c2m = b.node("GeometryNodeCurveToMesh", (1200, 400))
    b.feed(c2m, "Curve", out(setrad, "Curve"))
    b.feed(c2m, "Profile Curve", out(circle, "Curve"))
    b.feed(c2m, "Fill Caps", True)
    if any(s.name == "Scale" for s in c2m.inputs):
        # 4.2+ / 5.x: profile size comes from this input, wire the radius into it
        b.feed(c2m, "Scale", out(b.node("GeometryNodeInputRadius", (1000, 150)), "Radius"))

    m2v = b.node("GeometryNodeMeshToVolume", (1400, 150))
    set_enum(m2v, "resolution_mode", "VOXEL_SIZE", "Resolution Mode", "Size")
    b.feed(m2v, "Mesh", out(c2m, "Mesh"))
    b.feed(m2v, "Voxel Size", I("Fuse Voxel Size"))
    v2m = b.node("GeometryNodeVolumeToMesh", (1600, 150))
    b.feed(v2m, "Volume", out(m2v, "Volume"))

    switch = b.node("GeometryNodeSwitch", (1750, 400), "Fuse?", input_type="GEOMETRY")
    b.feed(switch, "Switch", I("Fuse Strands"))
    b.feed(switch, "False", out(c2m, "Mesh"))
    b.feed(switch, "True", out(v2m, "Mesh"))

    smooth = b.node("GeometryNodeSetShadeSmooth", (1850, 200))
    b.feed(smooth, 0, out(switch, "Output"))
    b.feed(go, 0, smooth.outputs[0])
    return ng


# ---------------------------------------------------------------- demo guide

def _roots():
    import math
    roots = []
    for i, deg in enumerate((20, 110, 200, 290)):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        reach = 0.9 + 0.15 * (i % 2)
        roots.append(([(0.0, 0.0, 0.45),
                       (0.35 * c, 0.35 * s, 0.05),
                       (0.7 * reach * c, 0.7 * reach * s, -0.3),
                       (reach * c, reach * s, -0.6)],
                      [1.3, 1.1, 0.8, 0.5]))
    return roots


DEMO_SPLINES = [
    # trunk
    ([(0.0, 0.0, -0.2), (0.08, 0.05, 0.6), (-0.06, 0.0, 1.3), (0.0, -0.04, 2.0), (0.02, 0.0, 2.3)],
     [2.0, 1.6, 1.35, 1.2, 1.0]),
    # main branches
    ([(0.0, 0.0, 2.05), (0.6, 0.05, 2.25), (1.3, -0.05, 2.3), (1.9, 0.05, 2.2), (2.4, 0.0, 1.95)],
     [1.2, 1.0, 0.85, 0.7, 0.5]),
    ([(0.0, 0.0, 2.1), (-0.65, -0.05, 2.3), (-1.35, 0.05, 2.25), (-1.95, -0.05, 2.3), (-2.45, 0.0, 2.1)],
     [1.2, 1.0, 0.85, 0.7, 0.5]),
    # twigs
    ([(1.2, 0.0, 2.3), (1.4, 0.1, 2.6), (1.5, 0.2, 2.85)], [0.6, 0.45, 0.3]),
    ([(-1.0, 0.0, 2.3), (-1.15, -0.1, 2.55), (-1.2, -0.15, 2.8)], [0.6, 0.45, 0.3]),
    ([(0.5, 0.0, 2.2), (0.6, -0.2, 2.5), (0.55, -0.35, 2.7)], [0.55, 0.4, 0.3]),
] + _roots()


def make_demo_guide():
    cu = bpy.data.curves.new("IntertwinedGuide", "CURVE")
    cu.dimensions = "3D"
    for pts, radii in DEMO_SPLINES:
        spline = cu.splines.new("BEZIER")
        spline.bezier_points.add(len(pts) - 1)
        for bp, co, r in zip(spline.bezier_points, pts, radii):
            bp.co = co
            bp.handle_left_type = bp.handle_right_type = "AUTO"
            bp.radius = r
    obj = bpy.data.objects.new("IntertwinedTree", cu)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = bpy.context.scene.cursor.location
    return obj


def main():
    ng = build_group()
    obj = bpy.context.view_layer.objects.active
    if obj is None or obj.type != "CURVE":
        obj = make_demo_guide()
    if not any(m.type == "NODES" and m.node_group == ng for m in obj.modifiers):
        obj.modifiers.new(MOD_NAME, "NODES").node_group = ng
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    print(f"'{GROUP_NAME}' applied to {obj.name}")


if __name__ == "__main__":
    main()
