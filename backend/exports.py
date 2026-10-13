"""Publish verified exports, with rollback and recovery after a forced shutdown."""
import json
import os
from pathlib import Path
import shutil
import uuid

NAMES = {'final_video.mp4', 'final_video_3x4.mp4', 'final_video.srt', 'final_video.qa.json'}


def record(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def rollback(project, work, entries):
    for entry in entries:
        target = project / entry['target']
        if target.name not in NAMES or target.parent != project:
            raise ValueError('Invalid export recovery target.')
        backup = work / entry['backup'] if entry['backup'] else None
        if backup and not backup.resolve().is_relative_to(work.resolve()):
            raise ValueError('Invalid export recovery backup.')
        if not target.resolve().is_relative_to(project.resolve()):
            raise ValueError('Invalid export recovery target.')
        if backup and backup.is_file():
            backup.replace(target)
        elif not backup:
            target.unlink(missing_ok=True)


def publish(project, outputs, remove=()):
    work = project / '.studio/exports' / uuid.uuid4().hex
    work.mkdir(parents=True)
    if not work.resolve().is_relative_to(project.resolve()):
        raise ValueError('Export staging must stay inside the project.')
    entries = []
    clean = True
    try:
        for target in [*outputs, *remove]:
            if target.name not in NAMES or target.parent != project or not target.resolve().is_relative_to(project.resolve()):
                raise ValueError('Invalid export target.')
            backup = target.name + '.previous' if target.exists() else None
            if backup:
                shutil.copy2(target, work / backup)
            candidate = target.name + '.candidate' if target in outputs else None
            if candidate:
                shutil.copy2(outputs[target], work / candidate)
            entries.append({'target': target.name, 'backup': backup, 'candidate': candidate})
        journal = work / 'journal.json'
        record(journal, {'owner': os.getpid(), 'phase': 'publishing', 'entries': entries})
        clean = False
        try:
            for entry in entries:
                target = project / entry['target']
                if entry['candidate']:
                    (work / entry['candidate']).replace(target)
                else:
                    target.unlink(missing_ok=True)
            record(journal, {'owner': os.getpid(), 'phase': 'committed', 'entries': entries})
            clean = True
        except BaseException:
            rollback(project, work, entries)
            clean = True
            raise
    finally:
        if clean:
            shutil.rmtree(work, ignore_errors=True)


def recover(project):
    recovered = False
    for journal in (project / '.studio/exports').glob('*/journal.json'):
        work = journal.parent
        if not work.resolve().is_relative_to(project.resolve()):
            continue
        data = json.loads(journal.read_text(encoding='utf-8'))
        try:
            os.kill(data['owner'], 0)
            continue
        except (ProcessLookupError, PermissionError):
            pass
        if data['phase'] != 'committed':
            rollback(project, work, data['entries'])
            recovered = True
        shutil.rmtree(work, ignore_errors=True)
    return recovered
