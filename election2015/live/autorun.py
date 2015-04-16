import time
import datetime
import subprocess



while(True):

    print "\n" * 5
    print "********************"
    print datetime.datetime.now()
    execfile("update_data.py")
    print "********************"
    print "\n" * 5
    time.sleep(5)
    subprocess.call("autorun.sh", shell = True)
    for i in range(113):
        print i
        time.sleep(1)
