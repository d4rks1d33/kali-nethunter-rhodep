/*
 * nl80211-fake.c.inc  -- fake NETLINK_GENERIC / nl80211 responder for
 * qemu-user. Included by linux-user/syscall.c.
 *
 * qemu-user does not support PF_NETLINK/NETLINK_GENERIC. The WiFi Pineapple
 * Pager Go firmware uses mdlayher/genetlink (nl80211) only to enumerate radios
 * at boot. We create a socketpair in do_socket(), hand one end to the guest,
 * and run a tiny host-side responder thread on the other end that speaks the
 * minimal genl/nl80211 protocol and returns two fake wiphys (phy0->wlan0,
 * phy1->wlan1). Guest and host are both little-endian (mipsel on aarch64) so
 * no byte-swapping is needed.
 */
#include <pthread.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

/* wire structs (host native == guest native, both LE) */
struct qnl_nlmsghdr { uint32_t len; uint16_t type; uint16_t flags;
                      uint32_t seq; uint32_t pid; };
struct qnl_nlattr   { uint16_t len; uint16_t type; };
struct qnl_genlmsghdr { uint8_t cmd; uint8_t version; uint16_t reserved; };

#define QNL_ERROR 0x2
#define QNL_DONE  0x3
#define QNL_F_MULTI 2

#define QGENL_ID_CTRL 0x10
#define QCTRL_CMD_GETFAMILY 3
#define QCTRL_CMD_NEWFAMILY 1
#define QCTRL_ATTR_FAMILY_ID 1
#define QCTRL_ATTR_FAMILY_NAME 2
#define QCTRL_ATTR_VERSION 3
#define QCTRL_ATTR_HDRSIZE 4
#define QCTRL_ATTR_MAXATTR 5
#define QCTRL_ATTR_MCAST_GROUPS 7
#define QCTRL_ATTR_MCAST_GRP_NAME 1
#define QCTRL_ATTR_MCAST_GRP_ID 2

#define QNL80211_FAMILY_ID 0x1b

#define QNL80211_CMD_GET_WIPHY 1
#define QNL80211_CMD_NEW_WIPHY 3
#define QNL80211_CMD_GET_INTERFACE 5
#define QNL80211_CMD_NEW_INTERFACE 7

#define QNL80211_ATTR_WIPHY 1
#define QNL80211_ATTR_WIPHY_NAME 2
#define QNL80211_ATTR_IFINDEX 3
#define QNL80211_ATTR_IFNAME 4
#define QNL80211_ATTR_IFTYPE 5
#define QNL80211_ATTR_MAC 6
#define QNL80211_ATTR_GENERATION 46
#define QNL80211_IFTYPE_STATION 2
#define QNL80211_IFTYPE_MONITOR 6

#define QALIGN4(x) (((x)+3)&~3)

struct qradio { const char *wname; const char *ifname; uint32_t wiphy;
                uint32_t ifindex; uint32_t iftype; uint8_t mac[6]; };
static struct qradio q_radios[2] = {
  { "phy0","wlan0",0,3,QNL80211_IFTYPE_STATION,{0x00,0xc0,0xca,0x11,0x22,0x33}},
  { "phy1","wlan1",1,4,QNL80211_IFTYPE_MONITOR,{0x00,0xc0,0xca,0x44,0x55,0x66}},
};

static int qput_attr(uint8_t *b,int o,uint16_t t,const void*d,int l){
    struct qnl_nlattr *a=(struct qnl_nlattr*)(b+o);
    a->type=t; a->len=(uint16_t)(sizeof(*a)+l);
    memcpy(b+o+sizeof(*a),d,l);
    int tot=QALIGN4(sizeof(*a)+l);
    for(int i=sizeof(*a)+l;i<tot;i++) b[o+i]=0;
    return o+tot;
}
static int qput_u32(uint8_t*b,int o,uint16_t t,uint32_t v){return qput_attr(b,o,t,&v,4);}
static int qput_u16(uint8_t*b,int o,uint16_t t,uint16_t v){return qput_attr(b,o,t,&v,2);}

static int qemit(uint8_t*out,int off,uint16_t type,uint16_t flags,uint32_t seq,
                 uint32_t pid,uint8_t cmd,const uint8_t*attrs,int al){
    struct qnl_nlmsghdr *nh=(struct qnl_nlmsghdr*)(out+off);
    int hdr=sizeof(*nh)+sizeof(struct qnl_genlmsghdr);
    nh->type=type; nh->flags=flags; nh->seq=seq; nh->pid=pid;
    struct qnl_genlmsghdr *gh=(struct qnl_genlmsghdr*)(out+off+sizeof(*nh));
    gh->cmd=cmd; gh->version=1; gh->reserved=0;
    if(al) memcpy(out+off+hdr,attrs,al);
    nh->len=hdr+al;
    return off+QALIGN4(hdr+al);
}
static int qemit_done(uint8_t*out,int off,uint32_t seq,uint32_t pid){
    struct qnl_nlmsghdr *nh=(struct qnl_nlmsghdr*)(out+off);
    nh->len=sizeof(*nh)+4; nh->type=QNL_DONE; nh->flags=QNL_F_MULTI;
    nh->seq=seq; nh->pid=pid; *(int*)(out+off+sizeof(*nh))=0;
    return off+QALIGN4(sizeof(*nh)+4);
}

/* Build reply for one request message; returns reply length in out. */
static int qnl_build_reply(const uint8_t *req,int reqlen,uint8_t *out){
    if(reqlen<(int)sizeof(struct qnl_nlmsghdr)) return 0;
    const struct qnl_nlmsghdr *nh=(const struct qnl_nlmsghdr*)req;
    uint32_t seq=nh->seq, pid=nh->pid;
    uint16_t ftype=nh->type;
    const struct qnl_genlmsghdr *gh=(const struct qnl_genlmsghdr*)(req+sizeof(*nh));
    uint8_t cmd=gh->cmd;
    int off=0;
    uint8_t attrs[2048];

    if(ftype==QGENL_ID_CTRL && cmd==QCTRL_CMD_GETFAMILY){
        int ao=0; const char*fn="nl80211";
        ao=qput_attr(attrs,ao,QCTRL_ATTR_FAMILY_NAME,fn,strlen(fn)+1);
        ao=qput_u16(attrs,ao,QCTRL_ATTR_FAMILY_ID,QNL80211_FAMILY_ID);
        ao=qput_u32(attrs,ao,QCTRL_ATTR_VERSION,1);
        ao=qput_u32(attrs,ao,QCTRL_ATTR_HDRSIZE,0);
        ao=qput_u32(attrs,ao,QCTRL_ATTR_MAXATTR,300);
        {
            uint8_t mg[512]; int mo=0;
            const char*grps[6]={"config","scan","regulatory","mlme","vendor","nan"};
            for(int i=0;i<6;i++){
                uint8_t one[128]; int oo=0;
                oo=qput_u32(one,oo,QCTRL_ATTR_MCAST_GRP_ID,(uint32_t)(i+1));
                oo=qput_attr(one,oo,QCTRL_ATTR_MCAST_GRP_NAME,grps[i],strlen(grps[i])+1);
                mo=qput_attr(mg,mo,(uint16_t)(i+1),one,oo);
            }
            ao=qput_attr(attrs,ao,QCTRL_ATTR_MCAST_GROUPS,mg,mo);
        }
        off=qemit(out,off,QGENL_ID_CTRL,0,seq,pid,QCTRL_CMD_NEWFAMILY,attrs,ao);
        return off;
    }
    if(ftype==QNL80211_FAMILY_ID && cmd==QNL80211_CMD_GET_WIPHY){
        for(int i=0;i<2;i++){
            int ao=0;
            ao=qput_u32(attrs,ao,QNL80211_ATTR_WIPHY,q_radios[i].wiphy);
            ao=qput_attr(attrs,ao,QNL80211_ATTR_WIPHY_NAME,q_radios[i].wname,strlen(q_radios[i].wname)+1);
            ao=qput_u32(attrs,ao,QNL80211_ATTR_GENERATION,1);
            off=qemit(out,off,QNL80211_FAMILY_ID,QNL_F_MULTI,seq,pid,QNL80211_CMD_NEW_WIPHY,attrs,ao);
        }
        off=qemit_done(out,off,seq,pid);
        return off;
    }
    if(ftype==QNL80211_FAMILY_ID && cmd==QNL80211_CMD_GET_INTERFACE){
        for(int i=0;i<2;i++){
            int ao=0;
            ao=qput_u32(attrs,ao,QNL80211_ATTR_IFINDEX,q_radios[i].ifindex);
            ao=qput_attr(attrs,ao,QNL80211_ATTR_IFNAME,q_radios[i].ifname,strlen(q_radios[i].ifname)+1);
            ao=qput_u32(attrs,ao,QNL80211_ATTR_WIPHY,q_radios[i].wiphy);
            ao=qput_u32(attrs,ao,QNL80211_ATTR_IFTYPE,q_radios[i].iftype);
            ao=qput_attr(attrs,ao,QNL80211_ATTR_MAC,q_radios[i].mac,6);
            ao=qput_u32(attrs,ao,QNL80211_ATTR_GENERATION,1);
            off=qemit(out,off,QNL80211_FAMILY_ID,QNL_F_MULTI,seq,pid,QNL80211_CMD_NEW_INTERFACE,attrs,ao);
        }
        off=qemit_done(out,off,seq,pid);
        return off;
    }
    /* default: ACK (NLMSG_ERROR err=0 echoing original header) */
    {
        struct qnl_nlmsghdr *e=(struct qnl_nlmsghdr*)(out);
        int elen=sizeof(*e)+4+sizeof(*nh);
        e->len=elen; e->type=QNL_ERROR; e->flags=0; e->seq=seq; e->pid=pid;
        *(int*)(out+sizeof(*e))=0;
        memcpy(out+sizeof(*e)+4,nh,sizeof(*nh));
        return QALIGN4(elen);
    }
}

static void *qnl_responder_thread(void *arg){
    int fd=(int)(intptr_t)arg;
    uint8_t req[16384];
    uint8_t rep[16384];
    for(;;){
        ssize_t n=read(fd,req,sizeof(req));
        if(n<=0){ if(n<0 && (errno==EINTR)) continue; break; }
        /* A single datagram may contain multiple nlmsgs (dump requests are
         * single, but be safe). Process each and concatenate replies. */
        int off=0, rlen=0;
        while(off+(int)sizeof(struct qnl_nlmsghdr)<=n){
            const struct qnl_nlmsghdr *nh=(const struct qnl_nlmsghdr*)(req+off);
            int mlen=nh->len; if(mlen<(int)sizeof(*nh)||off+mlen>n) mlen=n-off;
            int r=qnl_build_reply(req+off,mlen,rep+rlen);
            rlen+=r;
            off+=QALIGN4(mlen);
            if(mlen==0) break;
        }
        if(rlen>0){
            ssize_t w=write(fd,rep,rlen);
            (void)w;
        }
    }
    close(fd);
    return NULL;
}

/* Spawn the responder on the qemu-kept end of the socketpair. */
static void qemu_nl80211_start_responder(int host_fd){
    pthread_t t;
    pthread_attr_t a;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    pthread_create(&t, &a, qnl_responder_thread, (void*)(intptr_t)host_fd);
    pthread_attr_destroy(&a);
}
