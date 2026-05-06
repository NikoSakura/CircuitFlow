from __future__ import annotations
from typing import Dict, List, Tuple
import networkx as nx
from .model import Design, Component, Net


class ConnectivityGraph:
    """Build and query connectivity graph from Design nets."""

    def __init__(self, design: Design):
        self.design = design
        self.graph = nx.Graph()
        self._build()

    def _build(self):
        """Build graph where nodes are component references and edges are nets."""
        for ref in self.design.components:
            self.graph.add_node(ref)

        for net_name, net in self.design.nets.items():
            refs = list(set(p.component_ref for p in net.pins if p.component_ref in self.design.components))
            for i, r1 in enumerate(refs):
                for r2 in refs[i + 1:]:
                    if self.graph.has_edge(r1, r2):
                        self.graph[r1][r2]["nets"].append(net_name)
                    else:
                        self.graph.add_edge(r1, r2, nets=[net_name])

    def connected_components(self, ref: str) -> List[str]:
        """Return all references directly connected to ref."""
        if ref not in self.graph:
            return []
        return list(self.graph.neighbors(ref))

    def get_nets_between(self, ref1: str, ref2: str) -> List[str]:
        """Return net names connecting two components."""
        try:
            return self.graph[ref1][ref2].get("nets", [])
        except KeyError:
            return []

    def shortest_path(self, ref1: str, ref2: str) -> List[str] | None:
        """Return shortest component path between two references."""
        try:
            return nx.shortest_path(self.graph, ref1, ref2)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    def component_degree(self, ref: str) -> int:
        return self.graph.degree(ref) if ref in self.graph else 0

    def most_connected(self, n: int = 5) -> List[Tuple[str, int]]:
        """Return N most connected components."""
        return sorted(self.graph.degree(), key=lambda x: x[1], reverse=True)[:n]
