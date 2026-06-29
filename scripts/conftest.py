"""pytest configuration for scripts/.

Добавляет scripts/ в sys.path чтобы pytest мог импортировать
пакеты core, commands, checks напрямую — так же как это делает
python3 -m unittest discover tests.
"""
import sys
from pathlib import Path

# scripts/ → sys.path[0] чтобы import core, commands, checks работал
sys.path.insert(0, str(Path(__file__).resolve().parent))
