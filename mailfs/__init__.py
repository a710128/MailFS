from .mail import MailManager
from . import fuse
import threading
import os, json, time
from stat import S_IFDIR, S_IFLNK, S_IFREG
import pickle, errno
import time

class RamFile(object):
    def __init__(self, name):
        self.name = name
        self.data = b""
        self.xattr = {}
        self.mode = 0
        self.size = 0
        now = time.time()
        self.ctime = now
        self.mtime = now
        self.atime = now

class MailFS(fuse.Operations):
    def __init__(self, config, mount, storage="./storage", timeout=None, retry=2):
        self.storage = storage
        self.mount = mount
        if not os.path.exists(storage):
            os.makedirs(storage)
        self.metafile = os.path.join(storage, "meta.json")
        if not os.path.exists(self.metafile):
            json.dump({}, open(self.metafile, "w"))
        self.meta = json.load(open(self.metafile, "r"))
        self.managers = {}
        self.maillock = {}
        self.ramfs = {}
        self.fd = 0

        for it in config.keys():
            self.ensure_dir(os.path.join(storage, it))
            self.managers[it] = MailManager(config[it], timeout=timeout, retry=retry)
            if it not in self.meta:
                self.meta[it] = {}
            self.maillock[it] = threading.Lock()
            self.ramfs[it] = {}
        
        self.udp_thd = threading.Thread(target=self.update_loop)
        self.udp_thd.start()
    
    def serve(self):
        fuse.FUSE(self, self.mount, foreground=True, allow_other=True)

    def ensure_dir(self, path):
        if not os.path.exists( path ):
            os.makedirs(path)
    
    def update_mails(self, force=False):
        for kw, manager in self.managers.items():
            with self.maillock[kw]:
                for box in manager.do_list():
                    if box not in self.meta[kw]:
                        self.meta[kw][box] = {}
                    manager.do_select(box)
                    mlist = manager.do_getsearch()

                    if len(mlist) == 0: # empty box
                        self.meta[kw][box] = {}
                        continue
                    qs = "%d:%d" % (mlist[0], mlist[-1])
                    uids = manager.do_getuid(qs)

                    uid_idx = list(zip(uids, list(range(mlist[0], mlist[-1] + 1))))
                    if not force:
                        # remove items that already exist
                        uids = set(list(map(lambda x:x[0], uid_idx)))

                        # process removed emails
                        rm_keys = []
                        print("%s.%s" % (kw, box))
                        print(uids)
                        for uid in self.meta[kw][box].keys():
                            if uid not in uids:
                                rm_keys.append(uid)
                        for uid in rm_keys:
                            del self.meta[kw][box][uid]
                        
                        for uid, idx in uid_idx:
                            msg = manager.do_getmessageinfo(idx)[0]
                            msg["uid"] = uid
                            msg["mailbox"] = box
                            msg["site"] = kw
                            del msg["files"]
                            self.meta[kw][box][uid] = msg
                    else:
                        for msg, uid in zip(manager.do_getmessageinfo(qs), uids):
                            msg["uid"] = uid
                            msg["mailbox"] = box
                            msg["site"] = kw
                            del msg["files"]
                            self.meta[kw][box][uid] = msg
                        
            # end maillock
    
    def update_loop(self):
        first = True
        while True:
            if first:
                self.update_mails(force=True)
                first = False
            else:
                self.update_mails()
            json.dump(self.meta, open(self.metafile, "w"))
            time.sleep(5 * 60)  # refresh every 5 minutes
    
    def getMetaFilename(self, msg):
        return msg["subject"] + "." + str(msg["uid"])
    
    def loadMessage(self, msg):
        with self.maillock[msg["site"]]:
            manager = self.managers[msg["site"]]
            manager.do_select(msg["mailbox"])
            mlist = manager.do_getsearch()

            if len(mlist) > 0:
                qs = "%d:%d" % (mlist[0], mlist[-1])
                uids = manager.do_getuid(qs)
                idx = mlist[uids.index(msg["uid"])]
                if idx == -1:
                    return False
                else:
                    mail = manager.do_getmessage(idx)
                    if len(mail) == 0:
                        return False
                    mail = mail[0]
                    self.ensure_dir( os.path.join(self.storage, msg["site"]) )
                    base_path = os.path.join(self.storage, msg["site"], "%s" % msg["uid"])
                    file_list = []
                    data_list = {}
                    for attachment, fname, data in mail["files"]:
                        if attachment:
                            data_list["file_" + fname] = data
                        else:
                            data_list[fname] = data
                        file_list.append((attachment, fname, len(data)))
                    pickle.dump(file_list, open(base_path + ".meta", "wb"))
                    pickle.dump(data_list, open(base_path + ".data", "wb"))
                    return True
            else:
                return False
    
    def getMessageMeta(self, msg):
        self.ensure_dir( os.path.join( self.storage, msg["site"] ) )
        pt = os.path.join(self.storage, msg["site"], "%s.meta" % msg["uid"])
        if not os.path.exists(pt):
            if not self.loadMessage(msg):
                return { "content": [], "files": [] }
        flist = pickle.load(open(pt, "rb"))
        ret = {
            "content": [],
            "files": []
        }
        for attachment, fname, size in flist:
            if attachment:
                ret["files"].append({
                    "name": fname,
                    "size": size
                })
            else:
                ret["content"].append({
                    "name": "content.%s" % fname,
                    "size": size
                })
        return ret
    
    def getMessageData(self, msg, fname):
        if fname.startswith("content."):
            fname = fname[ len("content."): ]
        else:
            fname = "file_" + fname
        pt = os.path.join(self.storage, msg["site"], "%s.data" % msg["uid"])
        if not os.path.exists(pt):
            if not self.loadMessage(msg):
                return None
        pkl = pickle.load(open(pt, "rb"))
        if fname not in pkl:
            return None
        return pkl[fname]
    
    def getFileInfoRam(self, path, path_split, ret):
        vv = self.ramfs
        for part in path_split:
            if isinstance(vv, dict) and (part in vv):
                vv = vv[part]
            else:
                return
        ret["exists"] = True
        if isinstance(vv, RamFile):
            ret["is_dir"] = False
            ret["name"] = vv.name
            ret["mode"] = vv.mode
            ret["size"] = vv.size
            ret["ctime"] = vv.ctime
            ret["atime"] = vv.atime
            ret["mtime"] = vv.mtime
            ret["file"] = vv
        else:
            ret["is_dir"] = True
            ret["files"] = list(vv.keys())

    def getFileInfoMsg(self, path, path_split, msg, ret):
        ss = self.getMessageMeta(msg)
        content_list = list(map(lambda x: x["name"], ss["content"])) 
        files_list = list(map(lambda x: x["name"], ss["files"])) 
        if len(path_split) == 4:    # /site/mailbox/belong/subject/
            ret["exists"] = True
            ret["is_dir"] = True
            ret["files"] = content_list
            if len(files_list) > 0:
                ret["files"].append("files")
        else:
            if path_split[4] in content_list:
                ret["exists"] = True
                ret["is_dir"] = False
                ret["name"] = path_split[4]
                ret["mode"] = S_IFREG | 0o444
                ret["size"] = ss["content"][ content_list.index(path_split[4]) ]["size"]
                ret["ctime"] = msg["date"]
                ret["mtime"] = msg["date"]
                ret["atime"] = msg["date"]
                ret["file"] = msg
            elif path_split[4] == "files":
                if len(path_split) == 5:    # /site/mailbox/belong/subject/files
                    ret["exists"] = True
                    ret["is_dir"] = True
                    ret["files"] = files_list
                elif (path_split[5] in files_list) and (len(path_split) == 6):
                    ret["exists"] =True
                    ret["is_dir"] = False
                    ret["name"] = path_split[5]
                    ret["mode"] = S_IFREG | 0o444
                    ret["size"] = ss["files"][ files_list.index(path_split[5]) ]["size"]
                    ret["ctime"] = msg["date"]
                    ret["mtime"] = msg["date"]
                    ret["atime"] = msg["date"]
                    ret["file"] = msg

    def getFileInfoBelong(self, path, path_split, belong_list, ret):
        if len(path_split) == 3:    # /site/mailbox/belong/
            ret["exists"] = True
            ret["is_dir"] = True
            ret["files"] = list(map(self.getMetaFilename, belong_list))
        else:
            found = None
            for msg in belong_list:
                if self.getMetaFilename(msg) == path_split[3]:
                    found = msg
            if found is not None:
                self.getFileInfoMsg(path, path_split, found, ret)

    def getFileInfoMailbox(self, path, path_split, meta, ret):
        if len(path_split) == 2:    # /site/mailbox/
            ret["exists"] = True
            ret["is_dir"] = True
            all_belongs = set()
            for _, msg in meta.items():
                all_belongs.add(msg["belong"])
            ret["files"] = list(all_belongs)
        else:
            belong_list = []
            for uid in meta.keys():
                if meta[uid]["belong"] == path_split[2]:
                    belong_list.append(meta[uid])
            if len(belong_list) == 0:
                pass
            else:
                self.getFileInfoBelong(path, path_split, belong_list, ret)

    def getFileInfoMailsite(self, path, path_split, meta, ret):
        if len(path_split) == 1:    # /site/
            ret["exists"] = True
            ret["is_dir"] = True
            ret["site"] = path_split[0]
            next_set = set(["send"] + list(meta.keys()))
            ret["files"] = list(next_set)
        else:
            if path_split[1] == "send":
                ret["site"] = path_split[0]
                ret["mail"] = False
                self.getFileInfoRam(path, [path_split[0]] + path_split[2:], ret)
            elif path_split[1] in meta:
                ret["site"] = path_split[0]
                self.getFileInfoMailbox(path, path_split, meta[path_split[1]], ret)

    def getFileInfo(self, path):
        if len(path) == 1:
            path_split = []
        else:
            path_split = path[1:].split(os.path.sep)
        ret = {
            "exists": False,
            "is_dir": False,
            "mode": S_IFDIR | 0o644,
            "mail": True,
            "site": ""
        }
        if len(path_split) == 0: # /
            ret["exists"] = True
            ret["is_dir"] = True
            ret["files"] = list(self.meta.keys())
        elif path_split[0] in self.meta.keys():    # mail account
            self.getFileInfoMailsite(path, path_split, self.meta[path_split[0]], ret)
        return ret
    
    def sendMail(self, path):
        path_split = path[1:].split(os.path.sep)
        if len(path_split) == 4:
            site = path_split[0]
            to_ = path_split[2]
            subject = path_split[3]
            vv = self.ramfs[site][to_][subject]

            from email.mime.multipart import MIMEMultipart
            from email.mime.base import MIMEBase
            import mimetypes

            m = MIMEMultipart()
            for name, val in vv.items():
                if isinstance(val, RamFile) and name.startswith("content."):
                    mime_type = mimetypes.guess_type(name)[0]
                    subtype = "plain"
                    if mime_type is not None:
                        subtype = mime_type.split("/")[-1]
                    part = MIMEBase("text", subtype)
                    part.set_payload(val.data)
                    m.attach(part)
            if ("files" in vv) and isinstance(vv["files"], dict):
                for name, val in vv["files"].items():
                    if isinstance(val, RamFile):
                        mime_type = mimetypes.guess_type(name)[0]
                        if mime_type is None:
                            maintype, subtype = "*", "*"
                        else:
                            maintype, subtype = mime_type.split("/")
                        part = MIMEBase(maintype, subtype)
                        part.set_payload(val.data)
                        part.add_header('Content-Disposition', 'attachment', filename = name)
                        m.attach(part)
            m["Subject"] = subject
            
            with self.maillock[site]:
                manager = self.managers[site]
                return manager.do_sendmail(to_, m)
        else:
            return False
    
    def chmod(self, path, mode):
        ret = self.getFileInfo(path)
        if mode & 0o777 == 0o777:
            if ret["exists"] and (not ret["mail"]) and ret["is_dir"]:
                if self.sendMail(path):
                    return 0
                else:
                    raise fuse.FuseOSError(errno.ENXIO)
            else:
                raise fuse.FuseOSError(errno.ENXIO)
        else:
            if (not ret["mail"]) and (not ret["is_dir"]):
                ret["file"].mode &= 0o770000
                ret["file"].mode |= mode
                return 0
            else:
                if ret["mail"]:
                    raise fuse.FuseOSError(errno.EROFS)
                return 0
    
    def chown(self, path, uid, gid):
        return

    def create(self, path, mode):
        vv = self.ramfs
        path, name = os.path.split(path)
        path_split = path[1:].split(os.path.sep)
        if len(path_split) < 2 or path_split[1] != "send":
            raise fuse.FuseOSError(errno.EACCES)
        else:
            path_split = [path_split[0]] + path_split[2:]
            for it in path_split:
                if isinstance(vv, dict) and (it in vv):
                    vv = vv[it]
                else:
                    raise fuse.FuseOSError(errno.EACCES)
            if isinstance(vv, dict):
                if name not in vv:
                    vv[name] = RamFile(name)
                    vv[name].mode = S_IFREG | mode
                    self.fd += 1
                    return self.fd
                else:
                    raise fuse.FuseOSError(errno.EEXIST)
            else:
                raise fuse.FuseOSError(errno.EACCES)
    
    def getattr(self, path, fh):
        msg = self.getFileInfo(path)
        if not msg["exists"]:
            raise fuse.FuseOSError(errno.ENOENT)
        ret = {
            "st_mode": msg["mode"],
            "st_nlink": 2 if msg["is_dir"] else 1,
            "st_size": 4 if msg["is_dir"] else msg["size"],
            "st_ctime": time.time() if msg["is_dir"] else msg["ctime"],
            "st_mtime": time.time() if msg["is_dir"] else msg["mtime"],
            "st_atime": time.time() if msg["is_dir"] else msg["atime"]
        }
        return ret
    
    def getxattr(self, path, name, position=0):
        msg = self.getFileInfo(path)
        if msg["exists"]:
            if msg["mail"] or msg["is_dir"]:
                attrs = {}
            else:
                attrs = msg["file"].xattr
        else:
            raise fuse.FuseOSError(errno.ENOENT)
        
        if name in attrs:
            return attrs[name]
        else:
            raise fuse.FuseOSError(errno.ENODATA)

    def listxattr(self, path):
        msg = self.getFileInfo(path)
        if msg["exists"]:
            if msg["mail"] or msg["is_dir"]:
                attrs = {}
            else:
                attrs = msg["file"].xattr
        else:
            raise fuse.FuseOSError(errno.ENOENT)
        return list(attrs.keys())
    
    def mkdir(self, path, mode):
        path, name = os.path.split(path)
        path_split = path[1:].split(os.path.sep)
        if len(path_split) < 2 or path_split[1] != "send":
            raise fuse.FuseOSError(errno.EACCES)
        else:
            path_split = [path_split[0]] + path_split[2:]
            vv = self.ramfs
            for it in path_split:
                if isinstance(vv, dict) and (it in vv):
                    vv = vv[it]
                else:
                    raise fuse.FuseOSError(errno.EACCES)
            if isinstance(vv, dict):
                if name not in vv:
                    vv[name] = {}
                else:
                    raise fuse.FuseOSError(errno.EEXIST)
            else:
                raise fuse.FuseOSError(errno.EACCES)
    
    def open(self, path, flags):
        self.fd += 1
        return self.fd
    
    def read(self, path, size, offset, fh):
        msg = self.getFileInfo(path)
        if not msg["exists"] or msg["is_dir"]:
            raise fuse.FuseOSError(errno.EACCES)
        if msg["mail"]:
            return self.getMessageData(msg["file"], msg["name"])[offset:offset+size]
        else:
            return msg["file"].data[offset:offset+size]

    def readdir(self, path, fh):
        msg = self.getFileInfo(path)
        if (not msg["exists"]) or (not msg["is_dir"]):
            raise fuse.FuseOSError(errno.EACCES)
        return ['.', '..'] + msg["files"]
    
    def readlink(self, path):
        return b''
    
    def removexattr(self, path, name):
        msg = self.getFileInfo(path)
        if msg["exists"]:
            if msg["mail"] or msg["is_dir"]:
                attrs = {}
            else:
                attrs = msg["file"].xattr
        else:
            raise fuse.FuseOSError(errno.ENOENT)
        if name in attrs:
            del attrs[name]
        else:
            raise fuse.FuseOSError(errno.ENODATA)
    
    def rename(self, old, new):
        msg = self.getFileInfo(old)
        if not msg["exists"]:
            raise fuse.FuseOSError(errno.ENOENT)
        old, name = os.path.split(old)
        msg = self.getFileInfo(old)
        if not msg["exists"]:
            raise fuse.FuseOSError(errno.ENOENT)
        if msg["mail"] or (not msg["is_dir"]):
            raise fuse.FuseOSError(errno.ENXIO)
        
        new, newname = os.path.split(new)
        msg = self.getFileInfo(new)
        if not msg["exists"]:
            raise fuse.FuseOSError(errno.ENOENT)
        if msg["mail"] or (not msg["is_dir"]):
            raise fuse.FuseOSError(errno.ENXIO)
        
        path_split = old[1:].split(os.path.sep)
        path_split = [path_split[0]] + path_split[2:]
        vv = self.ramfs
        for it in path_split:
            vv = vv[it]
        tmp = vv[name]
        del vv[name]

        path_split = new[1:].split(os.path.sep)
        path_split = [path_split[0]] + path_split[2:]
        vv = self.ramfs
        for it in path_split:
            vv = vv[it]
        vv[newname] = tmp
        return
    
    def rmdir(self, path):
        msg = self.getFileInfo(path)
        if (not msg["exists"]) or (not msg["is_dir"]):
            raise fuse.FuseOSError(errno.EACCES)

        path_split = path[1:].split(os.path.sep)
        if len(path_split) < 3 or path_split[1] != "send":
            raise fuse.FuseOSError(errno.EACCES)
        else:
            name = path_split[-1]
            path_split = [path_split[0]] + path_split[2:-1]
            vv = self.ramfs
            for it in path_split:
                vv = vv[it]
            del vv[ name ]
    
    def setxattr(self, path, name, value, options, position=0):
        msg = self.getFileInfo(path)
        if msg["exists"]:
            if msg["mail"] or msg["is_dir"]:
                attrs = {}
            else:
                attrs = msg["file"].xattr
        else:
            raise fuse.FuseOSError(errno.ENOENT)
        attrs[name] = value
    
    def statfs(self, path):
        return dict(f_bsize=512, f_blocks=4096, f_bavail=2048)
    
    def symlink(self, target, source):
        raise fuse.FuseOSError(errno.EIO)

    def truncate(self, path, length, fh=None):
        msg = self.getFileInfo(path)
        if not msg["exists"] or msg["mail"] or msg["is_dir"]:
            raise fuse.FuseOSError(errno.EACCES)
        
        msg["file"].data= msg["file"].data[:length].ljust(
            length, '\x00'.encode('ascii'))
        msg["file"].size = length
    
    def unlink(self, path):
        path, name = os.path.split(path)
        msg = self.getFileInfo(path)
        if (not msg["exists"]) or (not msg["is_dir"]):
            raise fuse.FuseOSError(errno.EACCES)

        path_split = path[1:].split(os.path.sep)
        if len(path_split) < 2 or path_split[1] != "send":
            raise fuse.FuseOSError(errno.EACCES)
        else:
            path_split = [path_split[0]] + path_split[2:]
            vv = self.ramfs
            for it in path_split:
                vv = vv[it]
            del vv[name]
    
    def utimens(self, path, times=None):
        now = time()
        atime, mtime = times if times else (now, now)
        msg = self.getFileInfo(path)
        if not msg["exists"]:
            raise fuse.FuseOSError(errno.EACCES)
        if msg["mail"] or msg["is_dir"]:
            return
        msg["file"].atime = atime
        msg["file"].mtime = mtime
    
    def write(self, path, data, offset, fh):
        msg = self.getFileInfo(path)
        if (not msg["exists"]) or msg["is_dir"]:
            raise fuse.FuseOSError(errno.EACCES)
        if msg["mail"]:
            raise fuse.FuseOSError(errno.ENXIO)

        msg["file"].data = (
            msg["file"].data[:offset].ljust(offset, '\x00'.encode('ascii'))
            + data
            + msg["file"].data[offset + len(data):])
        msg["file"].size = len(msg["file"].data)
        return len(data)
