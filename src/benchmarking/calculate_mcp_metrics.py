import json
import random

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from pydantic import BaseModel

from src.benchmarking.baseline import DialogueBaseline
from src.benchmarking.deserialize_mcp_data import MCPDataset
from src.benchmarking.llm_evaluation import ComparisonResult, LLMEvaluation
from src.benchmarking.semantic_similarity import SemanticSimilarity
from src.summarize_algorithms.memory_bank.dialogue_system import DialogueSystem


@dataclass
class RawSemanticData:
    precision: List[float] = field(default_factory=list)
    recall: List[float] = field(default_factory=list)
    f1: List[float] = field(default_factory=list)


@dataclass
class RawLLMData:
    faithfulness: List[float] = field(default_factory=list)
    informativeness: List[float] = field(default_factory=list)
    coherency: List[float] = field(default_factory=list)


@dataclass
class MetricStats:
    mean: float = 0.0
    std: float = 0.0
    min: float = 0.0
    max: float = 0.0
    count: int = 0

    @classmethod
    def from_values(cls, values: List[float]) -> "MetricStats":
        if not values:
            return cls()

        np_values = np.array(values)
        return cls(
            mean=float(np.mean(np_values)),
            std=float(np.std(np_values)),
            min=float(np.min(np_values)),
            max=float(np.max(np_values)),
            count=len(values),
        )


@dataclass
class SystemResults:
    semantic_precision: MetricStats = field(default_factory=MetricStats)
    semantic_recall: MetricStats = field(default_factory=MetricStats)
    semantic_f1: MetricStats = field(default_factory=MetricStats)

    llm_faithfulness: MetricStats = field(default_factory=MetricStats)
    llm_informativeness: MetricStats = field(default_factory=MetricStats)
    llm_coherency: MetricStats = field(default_factory=MetricStats)


@dataclass
class PairwiseResults:
    faithfulness: Dict[str, int] = field(
        default_factory=lambda: {"recsum": 0, "baseline": 0, "draw": 0}
    )
    informativeness: Dict[str, int] = field(
        default_factory=lambda: {"recsum": 0, "baseline": 0, "draw": 0}
    )
    coherency: Dict[str, int] = field(
        default_factory=lambda: {"recsum": 0, "baseline": 0, "draw": 0}
    )

    def get_total_count(self) -> int:
        return sum(self.faithfulness.values())


@dataclass
class MCPResults:
    metadata: Dict[str, Any] = field(default_factory=dict)
    recsum_results: SystemResults = field(default_factory=SystemResults)
    baseline_results: SystemResults = field(default_factory=SystemResults)
    pairwise_results: PairwiseResults = field(default_factory=PairwiseResults)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CalculateMCPMetrics:
    def __init__(self, n_samples: int = 30):
        self.dataset = MCPDataset(n_samples)
        self.recsum = DialogueSystem()
        self.baseline = DialogueBaseline()
        self.semantic_scorer = SemanticSimilarity()
        self.llm_scorer = LLMEvaluation()

        self._recsum_semantic_data = RawSemanticData()
        self._baseline_semantic_data = RawSemanticData()
        self._recsum_llm_data = RawLLMData()
        self._baseline_llm_data = RawLLMData()
        self._pairwise_data = PairwiseResults()

        self.message_count = 0
        self.n_samples = n_samples

        self._is_calculated = False

    @property
    def results(self) -> MCPResults:
        if not self._is_calculated:
            self.calculate()
        return MCPResults(
            metadata={
                "timestamp": datetime.now().isoformat(),
                "n_samples": self.n_samples,
                "message_count": self.message_count,
                "version": "1.0",
            },
            recsum_results=SystemResults(
                semantic_precision=MetricStats.from_values(
                    self._recsum_semantic_data.precision
                ),
                semantic_recall=MetricStats.from_values(
                    self._recsum_semantic_data.recall
                ),
                semantic_f1=MetricStats.from_values(self._recsum_semantic_data.f1),
                llm_faithfulness=MetricStats.from_values(
                    self._recsum_llm_data.faithfulness
                ),
                llm_informativeness=MetricStats.from_values(
                    self._recsum_llm_data.informativeness
                ),
                llm_coherency=MetricStats.from_values(self._recsum_llm_data.coherency),
            ),
            baseline_results=SystemResults(
                semantic_precision=MetricStats.from_values(
                    self._baseline_semantic_data.precision
                ),
                semantic_recall=MetricStats.from_values(
                    self._baseline_semantic_data.recall
                ),
                semantic_f1=MetricStats.from_values(self._baseline_semantic_data.f1),
                llm_faithfulness=MetricStats.from_values(
                    self._baseline_llm_data.faithfulness
                ),
                llm_informativeness=MetricStats.from_values(
                    self._baseline_llm_data.informativeness
                ),
                llm_coherency=MetricStats.from_values(
                    self._baseline_llm_data.coherency
                ),
            ),
            pairwise_results=self._pairwise_data,
        )

    def calculate(self) -> None:
        dialogues = self.dataset.sessions.copy()

        for i, dialogue in enumerate(dialogues):
            print(f"Processing dialogue {i + 1}/{len(dialogues)}")

            self._process_dialogue(dialogue, i)
        self._is_calculated = True

    def _process_dialogue(self, dialogue: list, dialogue_index: int) -> None:
        ideal_response = dialogue[-1].messages.pop()

        while dialogue[-1].messages:
            self.message_count += 1
            query = dialogue[-1].messages.pop()

            recsum_response = self.recsum.process_dialogue(
                dialogue, query.message
            ).response
            baseline_response = self.baseline.process_dialogue(dialogue, query.message)

            self._update_semantic_scores(
                recsum_response, baseline_response, ideal_response.message
            )

            context = str(dialogue[-1])
            memory = self.dataset.memory[dialogue_index][-1]

            self._update_llm_single_scores(
                recsum_response, baseline_response, context, memory
            )
            self._update_llm_pairwise_scores(
                context, memory, recsum_response, baseline_response
            )

    def _update_semantic_scores(
        self, recsum_response: str, baseline_response: str, ideal_response: str
    ) -> None:
        recsum_score = self.semantic_scorer.compute_similarity(
            recsum_response, ideal_response
        )
        self._recsum_semantic_data.recall.append(recsum_score.recall)
        self._recsum_semantic_data.precision.append(recsum_score.precision)
        self._recsum_semantic_data.f1.append(recsum_score.f1)

        baseline_score = self.semantic_scorer.compute_similarity(
            baseline_response, ideal_response
        )
        self._baseline_semantic_data.recall.append(baseline_score.recall)
        self._baseline_semantic_data.precision.append(baseline_score.precision)
        self._baseline_semantic_data.f1.append(baseline_score.f1)

    def _update_llm_single_scores(
        self, recsum_response: str, baseline_response: str, context: str, memory: str
    ) -> None:
        recsum_score = self.llm_scorer.evaluate_single(context, memory, recsum_response)
        self._recsum_llm_data.faithfulness.append(recsum_score.faithfulness_score)
        self._recsum_llm_data.informativeness.append(recsum_score.informativeness_score)
        self._recsum_llm_data.coherency.append(recsum_score.coherency_score)

        baseline_score = self.llm_scorer.evaluate_single(
            context, memory, baseline_response
        )
        self._baseline_llm_data.faithfulness.append(baseline_score.faithfulness_score)
        self._baseline_llm_data.informativeness.append(
            baseline_score.informativeness_score
        )
        self._baseline_llm_data.coherency.append(baseline_score.coherency_score)

    def _update_llm_pairwise_scores(
        self, context: str, memory: str, recsum_response: str, baseline_response: str
    ) -> None:
        randomize_order = random.random() < 0.5

        if randomize_order:
            score = self.llm_scorer.evaluate_pairwise(
                context, memory, recsum_response, baseline_response
            )
            self._update_pairwise_counts(score, recsum_first=True)
        else:
            score = self.llm_scorer.evaluate_pairwise(
                context, memory, baseline_response, recsum_response
            )
            self._update_pairwise_counts(score, recsum_first=False)

    def _update_pairwise_counts(self, score: BaseModel, recsum_first: bool) -> None:
        metrics = ["faithfulness", "informativeness", "coherency"]

        for metric in metrics:
            score_value = getattr(score, metric)
            result_dict = getattr(self._pairwise_data, metric)

            if score_value == ComparisonResult.RESPONSE_1_BETTER:
                winner = "recsum" if recsum_first else "baseline"
                result_dict[winner] += 1
            elif score_value == ComparisonResult.RESPONSE_2_BETTER:
                winner = "baseline" if recsum_first else "recsum"
                result_dict[winner] += 1
            else:
                result_dict["draw"] += 1

    def save_results_to_json(self, filepath: str = None) -> str:
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"mcp_results_{timestamp}.json"

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        results_dict = self.results.to_dict()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results_dict, f, indent=2, ensure_ascii=False)

        return filepath

    def print_results(self) -> None:
        print(f"\nProcessed {self.message_count} messages\n")

        results = self.results

        self._print_semantic_results(results)

        self._print_llm_single_results(results)
        self._print_llm_pairwise_results(results)

    def _print_semantic_results(self, results: MCPResults) -> None:
        print("=" * 50)
        print("SEMANTIC EVALUATION RESULTS")
        print("=" * 50)
        print(
            f"RecSum    - Precision: {results.recsum_results.semantic_precision.mean:.4f}"
            f" (±{results.recsum_results.semantic_precision.std:.4f}), "
            f"Recall: {results.recsum_results.semantic_recall.mean:.4f}"
            f" (±{results.recsum_results.semantic_recall.std:.4f}), "
            f"F1: {results.recsum_results.semantic_f1.mean:.4f}"
            f" (±{results.recsum_results.semantic_f1.std:.4f})"
        )
        print(
            f"Baseline  - Precision: {results.baseline_results.semantic_precision.mean:.4f}"
            f" (±{results.baseline_results.semantic_precision.std:.4f}), "
            f"Recall: {results.baseline_results.semantic_recall.mean:.4f}"
            f" (±{results.baseline_results.semantic_recall.std:.4f}), "
            f"F1: {results.baseline_results.semantic_f1.mean:.4f}"
            f" (±{results.baseline_results.semantic_f1.std:.4f})"
        )
        print()

    def _print_llm_single_results(self, results: MCPResults) -> None:
        print("=" * 50)
        print("LLM SINGLE EVALUATION RESULTS")
        print("=" * 50)
        print(
            f"RecSum    - Faithfulness: {results.recsum_results.llm_faithfulness.mean:.2f}"
            f" (±{results.recsum_results.llm_faithfulness.std:.2f}), "
            f"Informativeness: {results.recsum_results.llm_informativeness.mean:.2f}"
            f" (±{results.recsum_results.llm_informativeness.std:.2f}), "
            f"Coherency: {results.recsum_results.llm_coherency.mean:.2f}"
            f" (±{results.recsum_results.llm_coherency.std:.2f})"
        )
        print(
            f"Baseline  - Faithfulness: {results.baseline_results.llm_faithfulness.mean:.2f}"
            f" (±{results.baseline_results.llm_faithfulness.std:.2f}), "
            f"Informativeness: {results.baseline_results.llm_informativeness.mean:.2f}"
            f" (±{results.baseline_results.llm_informativeness.std:.2f}), "
            f"Coherency: {results.baseline_results.llm_coherency.mean:.2f}"
            f" (±{results.baseline_results.llm_coherency.std:.2f})"
        )
        print()

    def _print_llm_pairwise_results(self, results: MCPResults) -> None:
        total_count = results.pairwise_results.get_total_count()

        if total_count == 0:
            print("No pairwise evaluations completed.")
            return

        print("=" * 50)
        print("LLM PAIRWISE EVALUATION RESULTS")
        print("=" * 50)

        metrics = ["faithfulness", "informativeness", "coherency"]
        for metric in metrics:
            result_dict = getattr(results.pairwise_results, metric)
            recsum_wins = result_dict["recsum"]
            baseline_wins = result_dict["baseline"]
            draws = result_dict["draw"]

            print(
                f"{metric.capitalize():<15}: RecSum {recsum_wins}/{total_count}"
                f" ({recsum_wins / total_count * 100:.1f}%), "
                f"Baseline {baseline_wins}/{total_count} ({baseline_wins / total_count * 100:.1f}%), "
                f"Draws {draws}/{total_count} ({draws / total_count * 100:.1f}%)"
            )


def main() -> None:
    metric_calculator = CalculateMCPMetrics()

    print("Starting MCP metrics calculation...")
    metric_calculator.calculate()

    print("Calculation completed. Results:")
    metric_calculator.print_results()

    saved_path = metric_calculator.save_results_to_json()
    print(f"\nResults have been saved to: {saved_path}")


if __name__ == "__main__":
    main()
