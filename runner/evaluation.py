"""WebArena-Verified's own evaluator, using its unmodified installed dataset."""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def evaluator():
    from webarena_verified.api.internal.data_reader import WebArenaVerifiedDataReader
    from webarena_verified.api.internal.evaluator import WebArenaVerifiedEvaluator
    from webarena_verified.types.config import WebArenaVerifiedConfig, EnvironmentConfig
    config = WebArenaVerifiedConfig(environments={
        'reddit': EnvironmentConfig(urls=['http://127.0.0.1:9999']),
        'shopping_admin': EnvironmentConfig(urls=['http://localhost:7780/admin']),
    })
    return WebArenaVerifiedEvaluator(config=config, reader=WebArenaVerifiedDataReader(config))


def evaluate(task, har, claim):
    result = evaluator().evaluate_task(task_id=task['webarena_task_id'],
        network_trace=Path(har), agent_response={
            'task_type': 'mutate', 'status': 'SUCCESS' if claim == 'DONE' else 'UNKNOWN_ERROR',
            'retrieved_data': None,
            'error_details': None if claim == 'DONE' else 'Agent did not claim completion',
        })
    data = result.model_dump(mode='json')
    Path(har).with_name('eval.json').write_text(__import__('json').dumps(data, indent=2) + '\n')
    if str(result.status).lower() == 'error':
        raise RuntimeError('WebArena evaluator error; see eval.json')
    return result.score == 1.0, f'{result.status}; score={result.score:g}'
