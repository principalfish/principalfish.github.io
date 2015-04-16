import time
import datetime
import subprocess



while(True):
    print datetime.datetime.now()
    execfile("update_data.py")
    time.sleep(5)
    subprocess.call("autorun.sh", shell = True)
    time.sleep(113)
