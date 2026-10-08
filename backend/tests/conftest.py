"""테스트가 app 패키지를 찾게 합니다.

컨테이너에서는 /app 이 작업 디렉터리라 보통은 필요 없지만,
호스트의 가상환경에서 돌릴 때도 같게 동작하도록 한 줄 넣어 둡니다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
