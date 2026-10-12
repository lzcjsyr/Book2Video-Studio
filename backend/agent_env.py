"""Resolve the CLI's existing stage provider in an isolated process.

Only the native host receives stdout. Credentials never enter frontend state,
launch manifests, settings files, terminal output, or API responses.
"""
import json
from pathlib import Path
import sys
import tempfile


def resolve(engine, project, step):
    sys.path.insert(0,str(engine))
    import yaml
    from core.config import apply_yaml_config
    path=engine/'config.yaml' if (engine/'config.yaml').exists() else engine/'config.example.yaml'
    merged=yaml.safe_load(path.read_text(encoding='utf-8'))
    state=json.loads((project/'.studio/state.json').read_text(encoding='utf-8'))
    from server import engine_settings,migrate_state
    for section,values in engine_settings(migrate_state(state).get('settings',{})).items():merged.setdefault(section,{}).update(values)
    with tempfile.TemporaryDirectory() as temporary:
        runtime=Path(temporary)/'agent-config.yaml';runtime.write_text(yaml.safe_dump(merged,allow_unicode=True),encoding='utf-8');apply_yaml_config(runtime)
    from core.infra.ai.claude_agent import build_step1_agent_env,build_step4_hyperframes_agent_env
    return build_step1_agent_env() if step=='1' else build_step4_hyperframes_agent_env()

if __name__=='__main__':
    try:
        # argv[2] is the project folder, already resolved by the native host from the project service.
        engine=Path(sys.argv[1]).resolve();project=Path(sys.argv[2]).resolve()
        if not (project/'text').is_dir() or not (project/'.studio/state.json').is_file() or sys.argv[3] not in {'1','4'}:raise ValueError('Invalid stage context')
        print(json.dumps(resolve(engine,project,sys.argv[3])),flush=True)
    except Exception as exc:
        print(str(exc),file=sys.stderr);sys.exit(1)
