# MAILFS

A simple mailfs based on FUSE !!

## REQUIREMENTS
* python >= 3.6
* fuse

## INSTALLATION

### Install FUSE
* Linux: [libfuse](https://github.com/libfuse/libfuse)
* Mac OS X: [FUSEOSX](https://osxfuse.github.io/)

## USAGE

### File Structure

```
+ site
    |
    + mailbox1
    |       |
    |       + Sender
    |           |
    |           + Subject
    |               |
    |               + content.txt
    |               + content.html
    |               + ...
    |               + files
    |                   |
    |                   + file1
    |                   + ...
    + mailbox2
    + mailbox3
    + ...
    + send
        |
        + To Address
                |
                + Subject
                    |
                    + content.txt
                    + content.html
                    + ...
                    + files
                        |
                        + file1
                        + ...
```

### Arguments
```sh
python main.py mount [-s STORAGE] [-t TIMEOUT] [-r RETRY] [-c CONFIG]
```
* STORAGE: Path to somewhere to cache data. default = `./storage`.
* TIMEOUT: IMAP & SMTP socket connection timeout (s). default = `10` seconds.
* RETRY: Max times of IMAP & SMTP login retry.  default = `2` times
* CONFIG: Path to configuration json file. default = `config.json`.

### Configuration
```json
{
    "site name": {
        "suffix": "Email address suffix such as 'mails.tsinghua.edu.cn'",
        "account": "YOUR ACCOUNT",
        "password": "PASSWORD",
        "encoding": "Default encoding",
        "receive": {
            "host": "IMAP Server Address",
            "port": 993,
            "ssl": true
        },
        "send": {
            "host": "SMTP Server Address",
            "port": 465,
            "ssl": true
        }
    },
    "another site": {
        // ...
    }
}
```

### Mail Structure

* `/subject/content.*` : body of this email.
* `/subject/files/*` : attached files in this email.

### Send Mail

Use command `chmod 777 <site_name>/send/<to>/<subject>` to send an email after putting correct mail structured files to `<site_name>/send/<to>/<subject>`.

