"""Non-secret provider selection shared by transport and accounting."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def provider_name():
    name = os.environ.get('OSD_PROVIDER', 'opencode')
    if name not in ('opencode', 'lmuai'):
        raise ValueError('Unsupported OSD_PROVIDER')
    return name


def budget_path():
    return ROOT / 'runs' / ('budget_lmuai.sqlite' if provider_name() == 'lmuai' else 'budget.sqlite')


def model_config(config, model, provider=None):
    provider = provider or provider_name()
    if provider == 'opencode':
        return config['models'][model]
    try:
        return config['providers'][provider]['models'][model]
    except KeyError:
        raise ValueError('Model is not configured for selected provider') from None
