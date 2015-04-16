import time
import datetime
import subprocess



while(True):
    print datetime.datetime.now()
    execfile("update_data.py")
    subprocess.call("autorun.sh", shell = True)

    time.sleep(118)
