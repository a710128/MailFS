import imaplib
import socket
import email
import os, re
import smtplib

class IMAP4(imaplib.IMAP4):
    def __init__(self, host='', port=imaplib.IMAP4_PORT, timeout=None):
        self.timeout = timeout
        imaplib.IMAP4.__init__(self, host, port)

    def _create_socket(self):
        host = None if not self.host else self.host
        return socket.create_connection((host, self.port), timeout=self.timeout)



class IMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host='', port=imaplib.IMAP4_PORT, timeout=None):
        self.timeout = timeout
        imaplib.IMAP4_SSL.__init__(self, host, port)

    def _create_socket(self):
        sock = IMAP4._create_socket(self)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)



class MailManager(object):
    def __init__(self, config, timeout=None, retry=2):
        self.config = config
        self.timeout = timeout
        self.retry = retry
        self.do_connect_imap()
        self.do_connect_smtp()
    
    def do_list(self):
        def filter_list(lst):
            ret = []
            for st in lst:
                st = st.decode()
                rquote = st.rfind('"')
                lquote = st.rfind('"', 0, rquote)
                ret.append(st[lquote + 1: rquote])
            return ret
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.list()
                if status != "OK":
                    raise Exception("Get mailbox list failed.")
                return filter_list(ret)
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")
    
    def do_createmailbox(self, mailbox):
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.create(mailbox)
                if status != "OK":
                    raise Exception("Create mailbox failed.")
                return
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")
    
    def do_select(self, mailbox):
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.select('"%s"' % mailbox)
                if status != "OK":
                    raise Exception("Select mailbox failed.")
                return
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")

    def do_getmessage(self, idx_, qry_str="(RFC822)"):
        def parse_email(ipt):
            ret = []
            for response in ipt:
                if isinstance(response, tuple):
                    msg = email.message_from_bytes(response[1])
                    if msg["Subject"] is not None:
                        subject, enc = email.header.decode_header(msg["Subject"])[0]
                    else:
                        subject, enc = "", ""
                    if enc == "unknown-8bit":
                        try_list = ["utf-8", "gbk", "gb2312"]
                        found = False
                        for try_enc in try_list:
                            try:
                                subject = subject.decode(try_enc)
                                found = True
                                break
                            except UnicodeDecodeError:
                                continue
                        if not found:
                            subject = "Unknown Subject Encoding"
                    else:
                        if isinstance(subject, bytes):
                            subject = subject.decode(enc)
                    if msg["From"] is None:
                        from_ = ""
                    else:
                        from_ = msg["From"].split()[-1]
                        if from_[0] == "<" and from_[-1] == ">":
                            from_ = from_[1:-1]
                    
                    if msg["To"] is not None:
                        to_ = msg["To"].split()[-1]
                        if to_[0] == "<" and to_[-1] == ">":
                            to_ = to_[1:-1]
                    else:
                        to_ = ""
                    
                    belong = to_ if from_ == self.config["account"] else from_

                    if len(belong) == 0:
                        belong = self.config["account"]

                    if belong.endswith("@" + self.config["suffix"]):
                        belong = belong[:-len("@" + self.config["suffix"])]
                    
                    if msg["Date"] is not None:
                        date = email.utils.parsedate_to_datetime(msg["Date"]).timestamp()
                    else:
                        date = 0.0

                    files = []

                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            if "attachment" in content_disposition:
                                fname, enc = email.header.decode_header(part.get_filename())[0]
                                if isinstance(fname, bytes):
                                    fname = fname.decode(enc)
                                body = part.get_payload(decode=True)
                                files.append((True, fname, body))
                            elif content_type.startswith("text"):
                                try:
                                    body = part.get_payload(decode=True).decode(self.config["encoding"])
                                except UnicodeDecodeError:
                                    try_list = ["utf-8", "gbk", "gb2312"]
                                    found = False
                                    for try_enc in try_list:
                                        try:
                                            body = part.get_payload(decode=True).decode(try_enc)
                                            found = True
                                        except UnicodeDecodeError:
                                            continue
                                    if not found:
                                        body = "Decode error"
                                if "plain" in content_type:
                                    files.append((False, "txt", body.encode()))
                                elif "html" in content_type:
                                    files.append((False, "html", body.encode()))
                    else:
                        content_type = msg.get_content_type()
                        if "plain" in content_type:
                            try:
                                body = msg.get_payload(decode=True).decode(self.config["encoding"])
                            except UnicodeDecodeError:
                                try_list = ["utf-8", "gbk", "gb2312"]
                                found = False
                                for try_enc in try_list:
                                    try:
                                        body = msg.get_payload(decode=True).decode(try_enc)
                                        found = True
                                    except UnicodeDecodeError:
                                        continue
                                if not found:
                                    body = "Decode error"
                                    
                            if "plain" in content_type:
                                files.append((False, "txt", body.encode()))
                            elif "html" in content_type:
                                files.append((False, "html", body.encode()))
                    ret.append({
                        "from": from_,
                        "to": to_,
                        "subject": subject,
                        "belong": belong,
                        "date": date,
                        "files": files
                    })
                # endif isinstance tuple
            # endfor
            return ret

        if isinstance(idx_, int):
            idx_ = str(idx_)
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.fetch(idx_, qry_str)
                if status != "OK":
                    raise Exception("Fetch mail failed.")
                return parse_email(ret)
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")

    def do_getsearch(self, filter_str='ALL'):
        def parse_list(lst):
            return list(map(int, lst[0].decode().split())) 
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.search(None, filter_str)
                if status != "OK":
                    raise Exception("Search mail failed.")
                return parse_list(ret)
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")
    
    def do_getuid(self, idx_):
        def parse_uid(data):
            pattern_uid = re.compile('\d+ \(UID (?P<uid>\d+)\)')
            ret = []
            for line in data:
                line = line.decode()
                match = pattern_uid.match(line)
                ret.append(match.group('uid'))
            return ret

        if isinstance(idx_, int):
            idx_ = str(idx_)
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.fetch(idx_, "(UID)")
                if status != "OK":
                    raise Exception("Get mail uid failed.")
                return parse_uid(ret)
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")

    def do_copy(self, idx_, mailbox):
        uid = self.do_getuid(idx_)[0]
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.uid("COPY", uid, '"%s"' % mailbox)
                if status != "OK":
                    raise Exception("Copy email failed.")
                return
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")

    def do_delete(self, idx_):
        uid = self.do_getuid(idx_)[0]
        for i in range(self.retry):
            try:
                status, ret = self.conn_imap.uid("STORE", uid, "+FLAGS", "(\Deleted)")
                if status != "OK":
                    raise Exception("Delete mail failed.")
                status, ret = self.conn_imap.expunge()
                if status != "OK":
                    raise Exception("Delete mail failed.")
                return
            except (IMAP4.error, socket.timeout):
                self.do_connect_imap()
        raise Exception("Connection error")

    def do_getmessageinfo(self, idx_):
        return self.do_getmessage(idx_, "(RFC822.SIZE BODY[HEADER.FIELDS (SUBJECT FROM TO DATE)])")
    
    def do_sendmail(self, to_, mail):
        if to_.find("@") == -1:
            to_ = to_ + "@" + self.config["suffix"]
        for i in range(self.retry):
            try:
                self.conn_smtp.sendmail(self.config["account"], [to_], mail.as_bytes())
                return True
            except smtplib.SMTPServerDisconnected:
                self.do_connect_smtp()
        raise Exception("Connection error")
            

    
    def do_connect_imap(self):
        if self.config["receive"]["ssl"]:
            self.conn_imap = IMAP4_SSL(self.config["receive"]["host"], self.config["receive"]["port"], timeout=self.timeout)
        else:
            self.conn_imap = IMAP4(self.config["receive"]["host"], self.config["receive"]["port"], timeout=self.timeout)
        if self.conn_imap.login(self.config["account"], self.config["password"])[0] != "OK":
            raise Exception("Login failed.")
    
    def do_connect_smtp(self):
        if self.config["send"]["ssl"]:
            self.conn_smtp = smtplib.SMTP_SSL( self.config["send"]["host"], self.config["send"]["port"], timeout=self.timeout)
        else:
            self.conn_smtp = smtplib.SMTP( self.config["send"]["host"], self.config["send"]["port"], timeout=self.timeout)
        if self.conn_smtp.login(self.config["account"], self.config["password"])[0] != 235:
            raise Exception("Login failed.")

