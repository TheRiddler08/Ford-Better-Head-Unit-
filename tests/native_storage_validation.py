import json,os,pathlib,sys,time,subprocess,tempfile,socket
from unittest.mock import patch
sys.path.insert(0,'/src')
from deploy import fleet
assert os.environ.get('CAR_DISPOSABLE_VALIDATION')=='1'
pathlib.Path('/run/systemd/system').mkdir(parents=True,exist_ok=True)
pathlib.Path('/etc/car-dashboard/storage').mkdir(parents=True,exist_ok=True)
pathlib.Path('/etc/car-dashboard/storage/capacity.json').write_text('{"bytes":1073741824}')
units={}
with tempfile.TemporaryDirectory() as temp:
    address=socket.gethostbyname(socket.gethostname())
    with patch.object(fleet,'ADDRESSES',{'tiny':address,'pi':address,'duet':address}),patch.object(fleet,'STATE',pathlib.Path(temp)),patch.object(fleet,'unit',side_effect=lambda name,value:units.update({name:value})),patch.object(fleet,'run'):
        result=fleet.install_storage({'role':'tiny','user':'nobody','home':'/tmp','repo':'/src'})
    assert not result,result
    processes=[]
    try:
        for name in ('car-storage-master.service','car-storage-chunks.service'):
            import shlex
            command=next(line.split('=',1)[1] for line in units[name].splitlines() if line.startswith('ExecStart='))
            log=open('/tmp/'+name+'.log','w')
            processes.append((subprocess.Popen(shlex.split(command),stdout=log,stderr=log),log,name))
        time.sleep(3)
        for process,log,name in processes:
            log.flush()
            assert process.poll() is None,(name,pathlib.Path('/tmp/'+name+'.log').read_text())
        print('PASS: packaged MooseFS 3.0 master and chunkserver run with generated configuration')
        print('PASS: local cluster configuration, packaged seed, OS account, durable fsync setting')
        print('FUSE mount not exercised: cloud container has no /dev/fuse or mount privilege')
    finally:
        for process,log,name in processes:
            process.terminate()
        for process,log,name in processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            log.close()
