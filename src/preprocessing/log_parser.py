"""Drain3 wrapper for online log template extraction.

This single class is reused across ALL datasets:
  - OTel Demo logs (primary pipeline, real-time via Kafka)
  - LogHub HDFS logs (pretraining preprocessing)

Sharing one LogParser instance builds a unified template vocabulary,
ensuring template IDs are consistent between pretraining (HDFS)
and fine-tuning (OTel Demo).
"""

from __future__ import annotations

from pathlib import Path

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig


class LogParser:
    """Drain3 wrapper that assigns monotonically increasing integer template IDs.

    Drain3's internal cluster IDs are alphanumeric (e.g. ``"A0042"``). This class
    maintains bidirectional ``template_to_id`` / ``id_to_template`` mappings using
    plain integers suitable as embedding indices in the LSTM-Autoencoder.
    """

    def __init__(self, persistence_path: str = "models/drain3/") -> None:
        Path(persistence_path).mkdir(parents=True, exist_ok=True)

        config = TemplateMinerConfig()
        config.drain_depth = 4
        config.drain_sim_th = 0.4
        config.drain_max_children = 100
        config.parametrize_numeric_tokens = True

        persistence = FilePersistence(f"{persistence_path}/drain3_state.bin")
        self.template_miner = TemplateMiner(persistence, config)

        self.template_to_id: dict[str, int] = {}
        self.id_to_template: dict[int, str] = {}
        self._next_id: int = 0

    def parse(self, log_line: str) -> tuple[int, str]:
        """Parse a raw log line and return ``(template_id, template_string)``.

        This is a *mutating* operation — new templates are added to the vocabulary.
        Strip timestamps/severity before passing for best results.
        """
        result = self.template_miner.add_log_message(log_line)
        template: str = result["template_mined"]

        if template not in self.template_to_id:
            self.template_to_id[template] = self._next_id
            self.id_to_template[self._next_id] = template
            self._next_id += 1

        return self.template_to_id[template], template

    def match(self, log_line: str) -> tuple[int, str]:
        """Match a log line to an existing template without updating the miner.

        Returns ``(-1, "UNKNOWN")`` if no matching template is found. ``-1`` is
        used rather than ``0`` because template IDs start at 0.

        Uses ``Drain.tree_search()`` internally — a read-only lookup that does
        NOT create new templates or update Drain3's internal state.
        """
        tokens = log_line.strip().split()
        cluster = self.template_miner.drain.tree_search(self.template_miner.drain.root_node, tokens)
        if cluster is None:
            return -1, "UNKNOWN"

        template: str = cluster.get_template()
        if template not in self.template_to_id:
            return -1, "UNKNOWN"

        return self.template_to_id[template], template

    def get_template(self, template_id: int) -> str:
        """Reverse lookup: template ID → template string."""
        return self.id_to_template.get(template_id, "UNKNOWN")

    @property
    def num_templates(self) -> int:
        """Total number of unique templates discovered so far."""
        return len(self.template_to_id)

    def save(self) -> None:
        """Persist current Drain3 state to disk."""
        self.template_miner.save_state("Drain3 template miner snapshot")
