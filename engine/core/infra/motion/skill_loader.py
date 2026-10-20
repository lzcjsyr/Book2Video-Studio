"""Project-owned references for HTML, GSAP and Three.js clips."""
STEP4_MAX_REFERENCE_READS = 3
STEP4_MOTION_RECIPES = tuple((name, f'motion-animation/rules/{name}.md') for name in (
    'counter_burst', 'split_reveal', 'cause_flow', 'camera_push', 'card_morph', 'radial_orbit', 'marker_trace'))
STEP4_OPTIONAL_REFERENCES = (
    ('Three.js、相机和模型动画', 'motion-core/references/three.md'),
    ('确定性与导出检查', 'motion-core/references/rendering.md'),
    ('字体和阅读层级', 'motion-creative/references/typography.md'),
)
