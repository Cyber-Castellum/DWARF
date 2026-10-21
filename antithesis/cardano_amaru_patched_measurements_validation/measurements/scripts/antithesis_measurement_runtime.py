"""Container adapter for the unchanged DWARF collectors and result lifecycle."""
from __future__ import annotations

import glob
import json
from pathlib import Path
import shutil
import time

from profile_manager.measurement_collectors.amaru_factory import build_amaru_measurement_factories
from profile_manager.measurement_collectors.cardano_factory import build_cardano_measurement_factories
from profile_manager.measurement_runtime import MeasurementRuntime
from profile_manager.antithesis_cardano_bridge import build_factories as build_cardano_compatible_factories

MAX_BYTES=32*1024*1024


def _copy_sources(patterns, destination):
    destination.mkdir(parents=True,exist_ok=True)
    candidates={p for pattern in patterns for p in glob.glob(pattern,recursive=True)
                if Path(p).is_file() and not Path(p).is_symlink()}
    # Retain recent rotations, then feed the existing last-verdict-wins
    # collectors in chronological rotation order.
    paths=sorted(candidates,key=lambda p:(Path(p).stat().st_mtime_ns,p))[-16:]
    copied=[];records=[]
    for index,name in enumerate(paths):
        path=Path(name);output=destination/str(index)
        with path.open('rb') as stream:
            size=path.stat().st_size
            if size>MAX_BYTES:
                stream.seek(-MAX_BYTES,2);stream.readline()
            start=stream.tell();data=stream.read(MAX_BYTES)
            output.write_bytes(data)
        copied.append(output)
        records.append({'path':name,'source_bytes':size,'copied_bytes':len(data),
                        'discarded_leading_bytes':start,'incomplete':start>0 or start+len(data)<size})
    window={'files':records,'omitted_file_count':max(0,len(candidates)-len(paths)),
            'max_files':16,'max_bytes_per_file':MAX_BYTES}
    window['incomplete']=bool(window['omitted_file_count'] or any(row['incomplete'] for row in records))
    return copied,paths,window


class WindowedCollector:
    """Retain adapter truncation evidence before the existing runtime reports it."""
    def __init__(self,collector,window):self.collector=collector;self.window=window
    def __getattr__(self,name):return getattr(self.collector,name)
    def finalize(self,context):
        result=self.collector.finalize(context)
        export=result.setdefault('export',{})
        export['adapter_source_window']=self.window
        if self.window['incomplete']:
            export['incomplete']=True
            truncated=set(export.get('truncated_sources',[]))
            truncated.update(row['path'] for row in self.window['files'] if row['incomplete'])
            export['truncated_sources']=sorted(truncated)
        context.write_json('result.json',result)
        return result


def collect(target, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    traces,inputs,stock_window=_copy_sources(target['trace_patterns'],output/'inputs/stock')
    patches,patch_inputs,patch_window=_copy_sources(target['patched_patterns'],output/'inputs/patched')
    window={'files':stock_window['files']+patch_window['files'],
            'omitted_file_count':stock_window['omitted_file_count']+patch_window['omitted_file_count'],
            'incomplete':stock_window['incomplete'] or patch_window['incomplete'],
            'max_files_per_stream':16,'max_bytes_per_file':MAX_BYTES}
    identity=target['identity']
    if identity['implementation']=='amaru':
        factories=build_amaru_measurement_factories(runtime_metadata_path=output/'runtime.json',
            target_node=target['service'],json_trace_paths=traces,otlp_trace_paths=[],
            tip_probe=None,target_identity=identity)
    else:
        factories=build_cardano_measurement_factories(runtime_metadata_path=output/'runtime.json',
            target_node=target['service'],trace_paths=traces,patched_trace_paths=patches,
            tip_probe=None,target_identity=None if identity.get('compatibility') else identity)
        if identity.get('compatibility'):
            factories.update(build_cardano_compatible_factories(json_trace_paths=patches,target_identity=identity))
    factories={name:(lambda entry,f=f:WindowedCollector(f(entry),window)) for name,f in factories.items()}
    # Start every existing collector at offset zero, including the header
    # collectors whose API deliberately has no include_existing argument.
    captured=[]
    for path in traces+patches:
        saved=path.with_suffix('.captured');path.rename(saved);path.touch()
        captured.append((path,saved))
    resolution={'target_identity':identity,'profile':None,'requested':target['entries'],
                'resolved':target['entries'],'skipped':[],'incompatible':[],'disabled':[]}
    runtime=MeasurementRuntime(run_dir=output,resolution=resolution,
        collector_factories=factories,scenario_id=target.get('scenario_id'))
    runtime.prepare();runtime.start()
    for path,saved in captured:
        saved.replace(path)
    summary=runtime.finalize()
    result={'service':target['service'],'input_files':inputs+patch_inputs,
            'source_available':bool(inputs or patch_inputs),'summary':summary,'source_window':window,
            'window':'bounded recent trace history; overlapping snapshots are not additive',
            'performance_authority':'Antithesis virtualized execution; not hardware benchmarking'}
    (output/'snapshot.json').write_text(json.dumps(result,sort_keys=True)+'\n')
    return result


def progress_summary(result):
    summary=result['summary'];collectors={}
    for identifier,body in summary.get('collector_results',{}).items():
        export=body.get('export',{})
        collectors[identifier]={'source_records':export.get('source_record_count'),
            'normalized_records':export.get('normalized_record_count'),
            'incomplete':export.get('incomplete',False),
            'measurements':{name:{key:value[key] for key in ('status','value','unit') if key in value}
                            for name,value in body.get('measurements',{}).items() if isinstance(value,dict)}}
    return {'dwarf_measurements':result['service'],'source_available':result['source_available'],
            'source_window_incomplete':result['source_window']['incomplete'],
            'collector_errors':summary.get('collector_errors',[]),'collectors':collectors,
            'artifacts':'/evidence/'+result['service']}


def main():
    config=json.loads(Path('/opt/dwarf/measurements.json').read_text())
    root=Path('/evidence');root.mkdir(parents=True,exist_ok=True)
    while True:
        for target in config['targets']:
            staging=root/(target['service']+'.next')
            shutil.rmtree(staging,ignore_errors=True)
            try:
                result=collect(target,staging)
                current=root/target['service'];previous=root/(target['service']+'.previous')
                shutil.rmtree(previous,ignore_errors=True)
                if current.exists():current.rename(previous)
                staging.rename(current)
                print(json.dumps(progress_summary(result)),flush=True)
            except Exception as exc:
                print(json.dumps({'dwarf_measurements':target['service'],'status':'unavailable',
                                  'reason':str(exc)}),flush=True)
        time.sleep(30)


if __name__=='__main__':
    main()
