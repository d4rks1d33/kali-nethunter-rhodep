import os,fcntl,struct,glob,time,sys,traceback
CREATE=(1<<30)|(40<<16)|(0xB5<<8)|0x01; DESTROY=(0xB5<<8)|0x02
N=int(sys.argv[1]); ctrl=sys.argv[2]
log=open("/home/kali/hammer2.log","w")
def say(m):
    log.write(m+"\n"); log.flush(); os.fsync(log.fileno())
    try: open("/dev/kmsg","w").write("HAMMER2: "+m+"\n")
    except Exception: pass
def up(): return open("/proc/uptime").read().split()[0]
try:
    for d in glob.glob("/dev/rpmsg[0-9]*"):
        try:
            f=os.open(d,os.O_RDWR); fcntl.ioctl(f,DESTROY); os.close(f)
        except Exception: pass
    time.sleep(0.5)
    before=set(glob.glob("/dev/rpmsg[0-9]*"))
    f=os.open(ctrl,os.O_RDWR)
    fcntl.ioctl(f,CREATE,struct.pack("32sII",b"DS",0xFFFFFFFF,0xFFFFFFFF)); os.close(f)
    time.sleep(0.5)
    dev=sorted(set(glob.glob("/dev/rpmsg[0-9]*"))-before)[-1]
    say("dev=%s N=%d uptime=%s  RAPID open/close, no delay" % (dev,N,up()))
    ok=fail=0
    for i in range(N):
        try:
            g=os.open(dev,os.O_RDWR); os.close(g); ok+=1
        except Exception as e:
            fail+=1
            if fail<4: say("cycle %d: %s" % (i,e))
        if i%25==0: say("cycle %d ok=%d fail=%d uptime=%s" % (i,ok,fail,up()))
    say("SURVIVED %d ok=%d fail=%d uptime=%s" % (N,ok,fail,up()))
except Exception:
    say("EXC "+traceback.format_exc())
