"""Procedural 3D city templates that run inside Blender (imports bpy).

Every builder function is deterministic: it receives a seeded random.Random
and only creates primitives (boxes, cylinders, tori, spheres), so polygon
counts stay low enough for a modest PC.
"""
import math
import random

import bpy

# Hard ceiling so a runaway plan cannot exhaust 8 GB RAM.
OBJECT_LIMIT = 500

STYLE_PROFILES = {
    'futuristic': {'window_bands': True, 'beacons': True},
    'robotic': {'window_bands': True, 'beacons': True},
    'modern': {'window_bands': False, 'beacons': False},
}


def hex_to_rgb(hex_color):
    value = hex_color.lstrip('#')
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def make_material(name, color, metallic=0.0, roughness=0.6, emission_color=None, emission_strength=0.0):
    material = bpy.data.materials.get(name)
    if material:
        return material
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['Roughness'].default_value = roughness
    emission = bsdf.inputs.get('Emission Color') or bsdf.inputs.get('Emission')
    if emission is not None and emission_color:
        emission.default_value = (*emission_color, 1.0)
        bsdf.inputs['Emission Strength'].default_value = emission_strength
    return material


class ObjectBudget:
    """Guards the total number of created objects."""

    def __init__(self):
        self.count = 0

    def add(self, obj):
        self.count += 1
        if self.count > OBJECT_LIMIT:
            bpy.data.objects.remove(obj, do_unlink=True)
            raise RuntimeError('Object limit of %d exceeded — the plan is too dense.' % OBJECT_LIMIT)
        return obj


class MaterialLibrary:
    """Creates every material once and reuses it for the whole city."""

    def __init__(self, plan):
        palette = plan.get('palette') or {}
        self.building = [
            make_material('BuildingMat_%d' % index, hex_to_rgb(hex_color), metallic=0.25, roughness=0.45)
            for index, hex_color in enumerate(palette.get('building_colors', ['#324a5e']))
        ]
        self.neon = [
            make_material('NeonMat_%d' % index, (0.01, 0.01, 0.01),
                          emission_color=hex_to_rgb(hex_color), emission_strength=5.0)
            for index, hex_color in enumerate(palette.get('neon_colors', ['#00c2ff']))
        ]
        self.ground = make_material('GroundMat', (0.02, 0.025, 0.035), roughness=0.9)
        self.asphalt = make_material('AsphaltMat', (0.015, 0.016, 0.02), roughness=0.85)
        self.sidewalk = make_material('SidewalkMat', (0.09, 0.095, 0.11), roughness=0.8)
        self.glass = make_material('GlassFacade', hex_to_rgb('#28405e'), metallic=0.7, roughness=0.2)
        self.metal = make_material('MetalStructure', (0.55, 0.56, 0.6), metallic=0.8, roughness=0.35)
        self.dark_metal = make_material('DarkMetal', (0.08, 0.09, 0.11), metallic=0.5, roughness=0.5)


def add_box(name, location, material, scale=(1.0, 1.0, 1.0)):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(material)
    return obj


def add_cylinder(name, radius, depth, location, material, vertices=10):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices, radius=radius, depth=depth, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(material)
    return obj


def build_ground(size, materials, budget):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = 'CityGround'
    ground.scale = (1.15, 1.15, 1.0)
    ground.data.materials.append(materials.ground)
    budget.add(ground)
    return ground


def build_roads(plan, materials, budget):
    ground_size = (plan.get('ground') or {}).get('size', 60.0)
    roads_plan = plan.get('roads') or {}
    spacing = roads_plan.get('spacing', 14.0)
    width = roads_plan.get('width', 3.0)
    sidewalk = roads_plan.get('sidewalk_width', 1.0)
    half = ground_size / 2.0

    count = 0
    lines = []
    position = spacing / 2.0
    while position < half:
        lines.extend((position, -position))
        position += spacing

    for axis in ('x', 'y'):
        for coordinate in lines:
            neon = materials.neon[int(abs(coordinate)) % len(materials.neon)]
            name_tag = '%s_%d' % (axis, int(coordinate * 10))
            if axis == 'x':
                road_scale = (width / 2.0, half, 1.0)
                lane_scale = (0.02, half * 0.95, 1.0)
                walks = ((-(width / 2.0 + sidewalk / 2.0), coordinate, 0.0),
                         (width / 2.0 + sidewalk / 2.0, coordinate, 0.0))
                walk_scale = (sidewalk / 2.0, half, 1.0)
            else:
                road_scale = (half, width / 2.0, 1.0)
                lane_scale = (half * 0.95, 0.02, 1.0)
                walks = ((coordinate, -(width / 2.0 + sidewalk / 2.0), 0.0),
                         (coordinate, width / 2.0 + sidewalk / 2.0, 0.0))
                walk_scale = (half, sidewalk / 2.0, 1.0)

            if axis == 'x':
                budget.add(add_box('Road_%s' % name_tag,
                                   (coordinate, 0, 0.01), materials.asphalt, scale=road_scale))
                budget.add(add_box('Lane_%s' % name_tag,
                                   (coordinate, 0, 0.03), neon, scale=lane_scale))
            else:
                budget.add(add_box('Road_%s' % name_tag,
                                   (0, coordinate, 0.01), materials.asphalt, scale=road_scale))
                budget.add(add_box('Lane_%s' % name_tag,
                                   (0, coordinate, 0.03), neon, scale=lane_scale))
            for index, walk_location in enumerate(walks):
                budget.add(add_box('Sidewalk_%s_%d' % (name_tag, index),
                                   (walk_location[0], walk_location[1], 0.02),
                                   materials.sidewalk, scale=walk_scale))
            count += 4
    return {'road_parts': count, 'road_count': len(lines) * 2}


def building_sites(ground_size, spacing):
    """Deterministic block centers between road lines, ordered center-outward."""
    half = ground_size / 2.0
    reach = int((half - spacing / 2.0) // spacing)
    sites = []
    for i in range(-reach, reach + 1):
        for j in range(-reach, reach + 1):
            x = i * spacing
            y = j * spacing
            if abs(x) < half and abs(y) < half:
                sites.append((x, y))
    sites.sort(key=lambda site: (abs(site[0]) + abs(site[1]), site[0], site[1]))
    return sites


def build_buildings(plan, materials, budget, rng, skip_sites):
    profile = STYLE_PROFILES.get(plan.get('style', 'futuristic'), STYLE_PROFILES['futuristic'])
    buildings_plan = plan.get('buildings') or {}
    wanted = buildings_plan.get('count', 10)
    height_min = buildings_plan.get('height_min', 4.0)
    height_max = buildings_plan.get('height_max', 12.0)
    footprint_min = buildings_plan.get('footprint_min', 2.0)
    footprint_max = buildings_plan.get('footprint_max', 5.0)
    ground_size = (plan.get('ground') or {}).get('size', 60.0)
    spacing = (plan.get('roads') or {}).get('spacing', 14.0)
    max_footprint = min(footprint_max, spacing * 0.45)

    sites = [site for site in building_sites(ground_size, spacing)
             if site not in skip_sites]
    ordered = sites[:]
    ordered.sort(key=lambda site: rng.random())  # deterministic shuffle via seeded rng

    stats = {'buildings': 0, 'window_bands': 0, 'beacons': 0}
    placed = 0
    for (center_x, center_y) in ordered:
        if placed >= wanted:
            break
        footprint_x = rng.uniform(footprint_min, max_footprint)
        footprint_y = rng.uniform(footprint_min, max_footprint)
        height = rng.uniform(height_min, height_max)
        if profile['window_bands']:
            body_material = materials.building[rng.randrange(len(materials.building))]
        else:
            body_material = materials.glass
        budget.add(add_box('Building_%d_%d' % (placed, int(height * 10)),
                           (center_x, center_y, height / 2.0), body_material,
                           scale=(footprint_x, footprint_y, height)))
        stats['buildings'] += 1
        placed += 1

        if profile['window_bands']:
            band_count = max(2, min(6, int(height // 1.4)))
            for band in range(band_count):
                fraction = 0.15 + 0.7 * band / max(1, band_count - 1)
                budget.add(add_box(
                    'WindowBand_%d_%d' % (placed, band),
                    (center_x, center_y, height * fraction),
                    materials.neon[(placed + band) % len(materials.neon)],
                    scale=(footprint_x + 0.06, footprint_y + 0.06, 0.06)))
                stats['window_bands'] += 1

        if profile['beacons'] and height > 8:
            budget.add(add_box('RoofBeacon_%d' % placed,
                               (center_x, center_y, height + 0.3),
                               materials.neon[(placed + 1) % len(materials.neon)],
                               scale=(0.25, 0.25, 0.6)))
            stats['beacons'] += 1
    site_index = len(ordered)
    # Second pass: if the plan wants more buildings than there are blocks,
    # allow smaller extra buildings offset inside the same blocks.
    extra_attempts = 0
    while placed < wanted and extra_attempts < len(ordered):
        center_x, center_y = ordered[extra_attempts]
        if (center_x, center_y) == (0.0, 0.0):
            extra_attempts += 1
            continue
        offset_x = rng.choice((-0.35, 0.35)) * spacing
        offset_y = rng.choice((-0.35, 0.35)) * spacing
        footprint = rng.uniform(footprint_min, max_footprint * 0.5)
        height = rng.uniform(height_min, height_max)
        budget.add(add_box('Building_%d_%d' % (site_index, int(height * 10)),
                           (center_x + offset_x, center_y + offset_y, height / 2.0),
                           materials.building[rng.randrange(len(materials.building))],
                           scale=(footprint, footprint, height)))
        stats['buildings'] += 1
        placed += 1
        site_index += 1
        if profile['window_bands']:
            band_count = max(2, min(5, int(height // 1.4)))
            for band in range(band_count):
                fraction = 0.15 + 0.7 * band / max(1, band_count - 1)
                budget.add(add_box(
                    'WindowBand_%d_%d' % (site_index, band),
                    (center_x + offset_x, center_y + offset_y, height * fraction),
                    materials.neon[(site_index + band) % len(materials.neon)],
                    scale=(footprint + 0.06, footprint + 0.06, 0.06)))
                stats['window_bands'] += 1
        extra_attempts += 1
    return placed, stats


def build_ai_tower(height, materials, budget):
    stats = {'tower_parts': 0}

    def part(obj):
        budget.add(obj)
        stats['tower_parts'] += 1
        return obj

    shaft_height = height * 0.55
    part(add_box('TowerBase', (0, 0, 0.6), materials.dark_metal, scale=(4.0, 4.0, 1.2)))
    part(add_cylinder('TowerShaft', 1.1, shaft_height,
                      (0, 0, 1.2 + shaft_height / 2.0), materials.metal, vertices=8))
    for level in (0.35, 0.6, 0.85):
        ring_z = 1.2 + shaft_height * level
        bpy.ops.mesh.primitive_torus_add(
            major_radius=1.35, minor_radius=0.09, location=(0, 0, ring_z))
        ring = bpy.context.active_object
        ring.name = 'TowerRing_%d' % int(level * 100)
        ring.data.materials.append(materials.neon[level_index(len(materials.neon), level)])
        budget.add(ring)
        stats['tower_parts'] += 1
    part(add_box('TowerCore', (0, 0, 1.2 + shaft_height + 0.7), materials.neon[0],
                 scale=(1.4, 1.4, 1.2)))
    part(add_cylinder('TowerAntenna', 0.08, height * 0.25,
                      (0, 0, 1.2 + shaft_height + 1.3 + height * 0.12), materials.metal))
    return stats


def level_index(count, level):
    return min(count - 1, int(level * count))


def build_robots(count, materials, budget, rng, sites, start_index, spacing):
    stats = {'robot_parts': 0}
    corner_offset = spacing * 0.26
    for index in range(count):
        center_x, center_y = sites[start_index + index]
        center_x += corner_offset
        center_y += corner_offset
        metal = materials.metal

        budget.add(add_box('RobotBody_%d' % (start_index + index),
                           (center_x, center_y, 1.0), metal, scale=(0.9, 0.55, 1.1)))
        budget.add(add_box('RobotHead_%d' % (start_index + index),
                           (center_x, center_y, 1.85), materials.dark_metal,
                           scale=(0.5, 0.45, 0.45)))
        for eye in (-0.12, 0.12):
            budget.add(add_box('RobotEye_%d_%d' % (start_index + index, int(eye * 100)),
                               (center_x, center_y + 0.26, 1.88), materials.neon[0],
                               scale=(0.07, 0.02, 0.07)))
        for side in (-1, 1):
            budget.add(add_box('RobotArm_%d_%d' % (start_index + index, side),
                               (center_x + side * 0.62, center_y, 1.05), metal,
                               scale=(0.18, 0.3, 0.85)))
            budget.add(add_box('RobotLeg_%d_%d' % (start_index + index, side),
                               (center_x + side * 0.25, center_y, 0.25), materials.dark_metal,
                               scale=(0.22, 0.28, 0.5)))
        budget.add(add_box('RobotChest_%d' % (start_index + index),
                           (center_x, center_y + 0.29, 1.15), materials.neon[index % len(materials.neon)],
                           scale=(0.28, 0.02, 0.28)))
        stats['robot_parts'] += 9
    return stats


def build_vehicles(count, materials, budget, rng, plan):
    ground_size = (plan.get('ground') or {}).get('size', 60.0)
    roads_plan = plan.get('roads') or {}
    spacing = roads_plan.get('spacing', 14.0)
    width = roads_plan.get('width', 3.0)
    reach = ground_size * 0.4
    lines = []
    position = spacing / 2.0
    while position < ground_size / 2.0:
        lines.extend((position, -position))
        position += spacing
    if not lines:
        lines = [0.0]

    stats = {'vehicle_parts': 0}
    for index in range(count):
        axis = rng.choice(('x', 'y'))
        coordinate = rng.choice(lines)
        along = rng.uniform(-reach, reach)
        lane = width / 4.0
        height = rng.uniform(2.5, 8.0)
        if axis == 'x':
            location = (along, coordinate + lane, height)
            yaw = 0.0
        else:
            location = (coordinate + lane, along, height)
            yaw = math.pi / 2.0
        tag = index
        body = add_box('Vehicle_%d' % tag, location, materials.metal, scale=(2.3, 1.1, 0.45))
        body.rotation_euler = (0.0, 0.0, yaw)
        budget.add(body)
        cabin = add_box('VehicleCabin_%d' % tag, location, materials.dark_metal,
                        scale=(1.0, 0.8, 0.35))
        cabin.location = (cabin.location.x, cabin.location.y, cabin.location.z + 0.35)
        cabin.rotation_euler = (0.0, 0.0, yaw)
        budget.add(cabin)
        glow = add_box('VehicleGlow_%d' % tag,
                       (location[0], location[1], location[2] - 0.35),
                       materials.neon[(index + 1) % len(materials.neon)],
                       scale=(2.0, 0.9, 0.05))
        glow.rotation_euler = (0.0, 0.0, yaw)
        budget.add(glow)
        stats['vehicle_parts'] += 3
    return stats


def build_lighting(plan):
    lighting = plan.get('lighting') or {}
    scene = bpy.context.scene
    world = bpy.data.worlds.new('CityWorld')
    world.use_nodes = True
    background = world.node_tree.nodes.get('Background')
    background.inputs['Color'].default_value = (
        *hex_to_rgb(lighting.get('world_color', '#070a12')), 1.0)
    background.inputs['Strength'].default_value = 1.0
    scene.world = world

    sun = bpy.data.lights.new('SunLight', type='SUN')
    sun.energy = lighting.get('sun_energy', 2.5)
    try:
        sun.angle = math.radians(lighting.get('sun_angle_deg', 45.0))
    except (TypeError, ValueError):
        sun.angle = math.radians(45.0)
    sun_obj = bpy.data.objects.new('Sun', sun)
    scene.collection.objects.link(sun_obj)
    sun_obj.rotation_euler = (math.radians(55.0), 0.1, math.radians(35.0))

    fill = bpy.data.lights.new('FillLight', type='AREA')
    fill.energy = 700
    fill.size = 14
    fill_obj = bpy.data.objects.new('FillLight', fill)
    scene.collection.objects.link(fill_obj)
    fill_obj.location = (6, -8, 16)
    fill_obj.rotation_euler = (0.65, 0.1, 0.6)
    return ['Sun', 'FillLight']


def materials_colors(plan, materials):
    lighting = plan.get('lighting') or {}
    return hex_to_rgb(lighting.get('world_color', '#070a12'))


def build_camera(plan, tower_height):
    camera_plan = plan.get('camera') or {}
    ground_size = (plan.get('ground') or {}).get('size', 60.0)
    distance = ground_size * camera_plan.get('distance_factor', 1.25)
    tilt = math.radians(camera_plan.get('tilt_deg', 35.0))
    azimuth = math.radians(camera_plan.get('rotation_deg', 45.0))

    location = (
        distance * math.cos(tilt) * math.cos(azimuth),
        -distance * math.cos(tilt) * math.sin(azimuth),
        distance * math.sin(tilt) + 1.0,
    )
    target = (0.0, 0.0, tower_height * 0.35 if tower_height else 4.0)
    data = bpy.data.cameras.new('CityCamera')
    data.lens = 50
    camera_obj = bpy.data.objects.new('CityCamera', data)
    bpy.context.scene.collection.objects.link(camera_obj)
    camera_obj.location = location
    camera_obj.rotation_euler = aim_at(camera_obj.location, target).to_euler()
    bpy.context.scene.camera = camera_obj
    return camera_obj


def aim_at(location, target):
    import mathutils
    direction = mathutils.Vector(target) - mathutils.Vector(location)
    return direction.to_track_quat('-Z', 'Y')


def build_city_scene(plan):
    """Full plan-driven city. Returns (object_names, summary, generation_seconds)."""
    import time
    started = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rng = random.Random(plan.get('seed', 1234) & 0x7FFFFFFF)
    budget = ObjectBudget()

    materials = MaterialLibrary(plan)
    ground_size = (plan.get('ground') or {}).get('size', 60.0)
    roads_plan = plan.get('roads') or {}
    spacing = roads_plan.get('spacing', 14.0)

    build_ground(ground_size, materials, budget)
    road_stats = build_roads(plan, materials, budget) if roads_plan.get('enabled', True) else \
        {'road_parts': 0, 'road_count': 0}

    skip = set()
    tower_plan = plan.get('ai_tower') or {}
    tower_height = 0.0
    tower_stats = {'tower_parts': 0}
    if tower_plan.get('enabled'):
        tower_height = tower_plan.get('height', 16.0)
        skip.add((0.0, 0.0))
    # The center block stays tower-exclusive; buildings avoid it either way.
    skip.add((0.0, 0.0))

    placed, building_stats = build_buildings(plan, materials, budget, rng, skip)
    if tower_plan.get('enabled'):
        tower_stats = build_ai_tower(tower_height, materials, budget)

    sites = building_sites(ground_size, spacing)
    sites = [site for site in sites if site not in skip]
    rng.shuffle(sites)
    robots_plan = plan.get('robots') or {}
    robot_count = robots_plan.get('count', 0)
    robot_stats = {'robot_parts': 0, 'robots': 0}
    if robot_count and len(sites) >= robot_count:
        robot_stats = build_robots(
            robot_count, materials, budget, rng, sites, start_index=0, spacing=spacing)
        robot_stats['robots'] = robot_count

    vehicles_plan = plan.get('vehicles') or {}
    vehicle_count = vehicles_plan.get('count', 0)
    vehicle_stats = build_vehicles(vehicle_count, materials, budget, rng, plan)

    lights = build_lighting(plan)
    camera = build_camera(plan, tower_height)

    object_names = [obj.name for obj in bpy.data.objects]
    summary = dict(road_stats)
    summary.update(building_stats)
    summary.update(tower_stats)
    summary.update(robot_stats)
    summary.update(vehicle_stats)
    summary.update({
        'scene_type': plan.get('scene_type', 'city'),
        'style': plan.get('style', 'futuristic'),
        'seed': plan.get('seed', 1234),
        'ground_size': ground_size,
        'camera': camera.name,
        'lights': lights,
        'total_objects': len(object_names),
    })
    return object_names, summary, time.time() - started
