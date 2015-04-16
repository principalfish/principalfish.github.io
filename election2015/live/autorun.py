import time
import datetime
import subprocess



while(True):
    print "********************"
    print datetime.datetime.now()
    execfile("update_data.py")
    print "********************"
    print "\n" * 5
    time.sleep(5)
    subprocess.call("autorun.sh", shell = True)
    for i in range(113):
        if 113 - i % 10 == 0:
            print "time to next update", 113 - i, "seconds"
        time.sleep(1)
