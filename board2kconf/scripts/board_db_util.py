import argparse
import click

import sys
from ..model import BoardDatabase

pass_bdb = click.make_pass_decorator(BoardDatabase)

@click.group
@click.pass_context
def bdb(ctx):
    ctx.obj = BoardDatabase()
    

@bdb.group(name="list")
def list_cmd(): ...

@list_cmd.command(name="mfr")
@pass_bdb
def list_manufacturers(bdb):
    for mfr in bdb.manufacturers:
        print(mfr)


@list_cmd.command
@pass_bdb
@click.argument("manufacturer")
def models(bdb, manufacturer):
    for board in bdb.models_from_manufacturer(manufacturer):
        print(board.model)

@list_cmd.command
@pass_bdb
@click.argument("manufacturer")
@click.argument("model")
def variants(bdb, manufacturer, model):
    for board in bdb.variants_from_mfr_board(manufacturer, model):
        print(board.variant or "Base Variant")

@bdb.command(name="show")
@pass_bdb
@click.argument("manufacturer")
@click.argument("model")
@click.argument("variant")
def show_board(bdb, manufacturer, model, variant):
    board = bdb.get(manufacturer, model, variant)
    print(board.pretty())



if __name__ == "__main__":
    sys.exit(bdb())
