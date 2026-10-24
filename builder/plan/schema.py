"""Scene-plan schema: validate a JSON plan and fill defaults.

Pure Python (no Django) so it stays testable and reusable.
A plan is only accepted if every value is inside hard limits sized for a
modest PC (8 GB RAM, no dedicated GPU).
"""
import re

class PlanValidationError(ValueError):
    """Raised with a list of human-readable problems when a plan is rejected."""

HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

STYLES = ('futuristic', 'robotic', 'modern')
SCENE_TYPES = ('city',)

LIMITS = {
    'ground_size': (20.0, 120.0),
    'road_spacing': (8.0, 40.0),
    'road_width': (2.0, 8.0),
    'sidewalk_width': (0.4, 3.0),
    'building_count': (1, 40),
    'building_height': (2.0, 30.0),
    'building_footprint': (1.0, 8.0),
    'tower_height': (6.0, 30.0),
    'robot_count': (0, 12),
    'vehicle_count': (0, 20),
    'resolution_x': (160, 1280),
    'resolution_y': (90, 720),
    'resolution_area': 1280 * 720,
    'samples': (8, 128),
    'sun_energy': (0.0, 10.0),
    'sun_angle_deg': (5.0, 89.0),
    'camera_distance_factor': (0.6, 1.6),
    'camera_tilt_deg': (10.0, 80.0),
    'camera_rotation_deg': (0.0, 360.0),
}

# Exact top-level keys a plan may contain — anything else is rejected so the planner cannot smuggle in unknown instructions.
ALLOWED_TOP_KEYS = {
    'scene_type', 'style', 'seed', 'ground', 'roads', 'buildings',
    'ai_tower', 'robots', 'vehicles', 'palette', 'lighting', 'camera', 'render',
}


def _number(value, limit_key, label, issues, as_int=False):
    if isinstance(value, bool):
        issues.append('%s must be a number, not a boolean.' % label)
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        issues.append('%s must be a number, got %r.' % (label, value))
        return None
    low, high = LIMITS[limit_key]
    if as_int:
        value = int(round(value))
    if value < low or value > high:
        issues.append('%s must be between %s and %s (got %s).' % (label, low, high, value))
        return None
    return value


def _hex_list(value, issues, label, minimum=1, maximum=8):
    if not isinstance(value, list) or len(value) < minimum:
        issues.append('%s must be a list of at least %d hex colors.' % (label, minimum))
        return []
    if len(value) > maximum:
        issues.append('%s may contain at most %d colors.' % (label, maximum))
        return []
    clean = []
    for item in value:
        if isinstance(item, str) and HEX_COLOR_RE.match(item):
            clean.append(item.lower())
        else:
            issues.append('%s contains an invalid hex color: %r.' % (label, item))
    return clean


def validate_plan(plan):
    """Validate a plan dict. Returns a normalized plan or raises PlanValidationError."""
    issues = []
    if not isinstance(plan, dict):
        raise PlanValidationError(['Plan must be a JSON object (dict).'])
    unknown = set(plan) - ALLOWED_TOP_KEYS
    if unknown:
        issues.append('Unknown plan keys: %s. Allowed keys: %s.' % (
            ', '.join(sorted(unknown)), ', '.join(sorted(ALLOWED_TOP_KEYS))))
        raise PlanValidationError(issues)

    out = {}
    out['scene_type'] = plan.get('scene_type', 'city')
    if out['scene_type'] not in SCENE_TYPES:
        issues.append('scene_type must be one of %s (got %r).' % (list(SCENE_TYPES), plan['scene_type']))

    out['style'] = plan.get('style', 'futuristic')
    if out['style'] not in STYLES:
        issues.append('style must be one of %s (got %r).' % (list(STYLES), plan['style']))

    out['seed'] = _to_int(plan.get('seed', 1234), 0, 999999999, 'seed', issues)

    ground = _section(plan, 'ground', issues)
    if ground is not None:
        out['ground'] = {
            'size': _number(ground.get('size', 60.0), 'ground_size', 'ground.size', issues),
        }

    roads = _section(plan, 'roads', issues)
    if roads is not None:
        out['roads'] = {
            'enabled': _to_bool(roads.get('enabled', True), 'roads.enabled', issues),
            'spacing': _number(roads.get('spacing', 14.0), 'road_spacing', 'roads.spacing', issues),
            'width': _number(roads.get('width', 3.0), 'road_width', 'roads.width', issues),
            'sidewalk_width': _number(roads.get('sidewalk_width', 1.0), 'sidewalk_width', 'roads.sidewalk_width', issues),
        }

    buildings = _section(plan, 'buildings', issues)
    if buildings is not None:
        out['buildings'] = {
            'count': _number(buildings.get('count', 10), 'building_count', 'buildings.count', issues, as_int=True),
            'height_min': _number(buildings.get('height_min', 4.0), 'building_height', 'buildings.height_min', issues),
            'height_max': _number(buildings.get('height_max', 12.0), 'building_height', 'buildings.height_max', issues),
            'footprint_min': _number(buildings.get('footprint_min', 2.0), 'building_footprint', 'buildings.footprint_min', issues),
            'footprint_max': _number(buildings.get('footprint_max', 5.0), 'building_footprint', 'buildings.footprint_max', issues),
        }

    tower = _section(plan, 'ai_tower', issues)
    if tower is not None:
        out['ai_tower'] = {
            'enabled': _to_bool(tower.get('enabled', False), 'ai_tower.enabled', issues),
            'height': _number(tower.get('height', 16.0), 'tower_height', 'ai_tower.height', issues),
        }

    robots = _section(plan, 'robots', issues)
    if robots is not None:
        out['robots'] = {
            'count': _number(robots.get('count', 0), 'robot_count', 'robots.count', issues, as_int=True),
        }

    vehicles = _section(plan, 'vehicles', issues)
    if vehicles is not None:
        out['vehicles'] = {
            'count': _number(vehicles.get('count', 0), 'vehicle_count', 'vehicles.count', issues, as_int=True),
        }

    palette = _section(plan, 'palette', issues)
    if palette is not None:
        out['palette'] = {
            'building_colors': _hex_list(
                palette.get('building_colors', ['#324a5e', '#46698b', '#6b7f9e', '#59625e', '#890c2a']),
                issues, 'palette.building_colors', minimum=1, maximum=8) or None,
            'neon_colors': _hex_list(
                palette.get('neon_colors', ['#00c2ff', '#7f5cff', '#ff2bd6']),
                issues, 'palette.neon_colors', minimum=1, maximum=5) or None,
        }

    lighting = _section(plan, 'lighting', issues)
    if lighting is not None:
        out['lighting'] = {
            'sun_energy': _number(lighting.get('sun_energy', 2.5), 'sun_energy', 'lighting.sun_energy', issues),
            'sun_angle_deg': _number(lighting.get('sun_angle_deg', 45.0), 'sun_angle_deg', 'lighting.sun_angle_deg', issues),
            'world_color': _hex(lighting.get('world_color', '#070a12'), issues, 'lighting.world_color'),
        }

    camera = _section(plan, 'camera', issues)
    if camera is not None:
        out['camera'] = {
            'distance_factor': _number(camera.get('distance_factor', 1.25), 'camera_distance_factor', 'camera.distance_factor', issues),
            'tilt_deg': _number(camera.get('tilt_deg', 35.0), 'camera_tilt_deg', 'camera.tilt_deg', issues),
            'rotation_deg': _number(camera.get('rotation_deg', 45.0), 'camera_rotation_deg', 'camera.rotation_deg', issues),
        }

    render = _section(plan, 'render', issues)
    if render is not None:
        res_x = _number(render.get('resolution_x', 960), 'resolution_x', 'render.resolution_x', issues, as_int=True)
        res_y = _number(render.get('resolution_y', 540), 'resolution_y', 'render.resolution_y', issues, as_int=True)
        if res_x is not None and res_y is not None:
            if res_x * res_y > LIMITS['resolution_area']:
                issues.append('render resolution %dx%d exceeds the %d-pixel budget for this PC.' % (
                    res_x, res_y, LIMITS['resolution_area']))
        out['render'] = {
            'resolution_x': res_x,
            'resolution_y': res_y,
            'samples': _number(render.get('samples', 32), 'samples', 'render.samples', issues, as_int=True),
        }

    out = {key: value for (key, value) in out.items() if value is not None}

    _cross_field_checks(out, issues)
    if issues:
        raise PlanValidationError(issues)
    return out


def _hex(value, issues, label):
    if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
        issues.append('%s must be a hex color like #0a0d16 (got %r).' % (label, value))
        return None
    return value.lower()


def _section(plan, key, issues):
    if key not in plan:
        return {}
    value = plan[key]
    if value is None:
        return {}
    if not isinstance(value, dict):
        issues.append('%s must be a JSON object (dict).' % key)
        return None
    return value


def _to_int(value, low, high, label, issues):
    try:
        value = int(value)
    except (TypeError, ValueError):
        issues.append('%s must be an integer (got %r).' % (label, value))
        return None
    if value < low or value > high:
        issues.append('%s must be between %d and %d.' % (label, low, high))
        return None
    return value


def _to_bool(value, label, issues):
    if not isinstance(value, bool):
        issues.append('%s must be true or false.' % label)
        return None
    return value


def _cross_field_checks(plan, issues):
    buildings = plan.get('buildings') or {}
    if buildings and buildings.get('height_min') is not None and buildings.get('height_max') is not None:
        if buildings['height_min'] > buildings['height_max']:
            issues.append('buildings.height_min must be <= buildings.height_max.')
    if buildings and buildings.get('footprint_min') is not None and buildings.get('footprint_max') is not None:
        if buildings['footprint_min'] > buildings['footprint_max']:
            issues.append('buildings.footprint_min must be <= buildings.footprint_max.')
    roads = plan.get('roads') or {}
    if roads and roads.get('enabled') and buildings and roads.get('spacing') is not None:
        spacing = roads['spacing']
        footprint_max = buildings.get('footprint_max') or 0
        if footprint_max and footprint_max > spacing * 0.8:
            issues.append('buildings.footprint_max (%s) must fit between roads (spacing %s).' % (footprint_max, spacing))


def plan_summary(plan: dict) -> dict:
    """A small human-readable digest used in result summaries."""
    return {
        'scene_type': plan.get('scene_type'),
        'style': plan.get('style'),
        'seed': plan.get('seed'),
        'buildings': (plan.get('buildings') or {}).get('count'),
        'has_ai_tower': bool((plan.get('ai_tower') or {}).get('enabled')),
        'robots': (plan.get('robots') or {}).get('count', 0),
        'vehicles': (plan.get('vehicles') or {}).get('count', 0),
        'ground_size': (plan.get('ground') or {}).get('size'),
        'resolution': [
            (plan.get('render') or {}).get('resolution_x'),
            (plan.get('render') or {}).get('resolution_y'),
        ],
    }
