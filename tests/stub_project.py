from vocode import state, settings as vocode_settings


class StubProject:
    def __init__(self) -> None:
        self.llm_usage = state.LLMUsageStats()
        self.settings = vocode_settings.Settings()

    def add_llm_usage(
        self,
        prompt_delta: int,
        completion_delta: int,
        cost_delta: float,
    ) -> None:
        stats = self.llm_usage
        stats.prompt_tokens += int(prompt_delta or 0)
        stats.completion_tokens += int(completion_delta or 0)
        stats.cost_dollars += float(cost_delta or 0.0)