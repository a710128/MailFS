from mailfs import MailFS
import json

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('mount')
    parser.add_argument("-s", "--storage", default="./storage", help="Path of directory to cache mails.")
    parser.add_argument("-t", "--timeout", default=10, type=int, help="Socket timeout (s).")
    parser.add_argument("-r", "--retry", default=2, type=int, help="Max times of mail connection retry.")
    parser.add_argument("-c", "--config", default="./config.json", help="Path to config file.")
    args = parser.parse_args()

    config = json.load(open(args.config, "r"))

    fs = MailFS(config, args.mount, storage=args.storage, timeout=args.timeout, retry=args.retry)
    print("running at %s" % args.mount)
    fs.serve()
    