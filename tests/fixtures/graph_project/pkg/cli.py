import click


@click.group()
def cli():
    pass


@cli.command()
def sub():
    pass


@click.command
def solo():
    pass


def main():
    cli()


class Runner:
    def go(self):
        main()
