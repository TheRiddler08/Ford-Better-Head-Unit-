"""Retire the known legacy user panel before enabling its system-service replacement."""
import os
import subprocess


def migrate():
    runtime=os.environ.get('XDG_RUNTIME_DIR') or '/run/user/'+str(os.getuid())
    env=dict(os.environ,XDG_RUNTIME_DIR=runtime)
    env.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+runtime+'/bus')
    command=['systemctl','--user']
    result=subprocess.run(command+['show','car-home-panel.service','--property=LoadState','--value'],
                          env=env,capture_output=True,text=True,timeout=15)
    if result.returncode or result.stdout.strip()!='loaded':
        return False
    subprocess.run(command+['disable','--now','car-home-panel.service'],env=env,check=True,timeout=45)
    print('Retired legacy car-home-panel user service; saved data and credential files were preserved.',flush=True)
    return True


if __name__=='__main__':migrate()
