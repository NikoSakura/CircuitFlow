from rich.console import Console
from rich.table import Table
from ..pcb.board_builder import PCBBoard

console = Console()


def display_board_summary(board: PCBBoard):
    """Display a formatted summary of the PCB."""
    table = Table(title=f"PCB: {board.name}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    for k, v in board.summary.items():
        if isinstance(v, float):
            table.add_row(k, f"{v:.1f}")
        else:
            table.add_row(k, str(v))

    console.print(table)


def display_net_list(board: PCBBoard):
    """Display net list."""
    table = Table(title="Nets")
    table.add_column("Code", style="cyan")
    table.add_column("Name", style="green")
    for name, code in board.nets.items():
        table.add_row(str(code), name)
    console.print(table)
