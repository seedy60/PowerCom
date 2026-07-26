"""PowerCom application startup."""

import sys


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"--config", "--config-ui", "--config-builder"}:
        from powercom_config import run

        run(args[1:])
        return

    import upgrade_conf
    del upgrade_conf

    from conf import conf
    from TTComCmd import TTComCmd

    conf.name = "PowerCom"
    conf.version = "2519"
    # Keep args out of the cmd system.
    # If -c is present, all subsequent args will be sent to the cmd system as a command.
    del sys.argv[1:]
    noAutoLogins = False
    shortnames = []
    while args:
        arg = args.pop(0)
        if arg == "-n":
            noAutoLogins = True
        elif arg == "-c":
            if shortnames:
                sys.exit("Logins and -c may not be mixed")
            noAutoLogins = True
            sys.argv.extend(args)
            args.clear()
        else:
            noAutoLogins = True
            shortnames.append(arg)
    app = TTComCmd(noAutoLogins, shortnames)
    app.allowPython()
    if shortnames:
        cur = shortnames[-1]
        app.onecmd("switch " + cur)
    app.run()
