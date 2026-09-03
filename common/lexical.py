"""
СПІЛЬНЕ · пошук по словах. Точка відліку, з якою порівнюється векторний пошук.

Курсовий `domain/knowledge.py` рахує збіг як `exact * 2 + partial` по рукописних
списках ключових слів. Для чотирнадцяти правил, до кожного з яких автор сам
приписав ключі, цього досить. Тут ключів немає й бути не може: документів
сімдесят два, фрагментів понад чотири тисячі, і виписувати до кожного список слів
руками — робота, якої ніхто не робить.

Тому точкою відліку тут є BM25, той самий алгоритм, на якому працює звичайний
повнотекстовий пошук у Postgres, Elasticsearch і Lucene. Він рахує три речі:

  скільки разів слово запиту трапилось у фрагменті — але з насиченням: десяте
    входження додає майже нічого порівняно з другим (коефіцієнт k1);
  наскільки слово рідкісне в усіх документах — «prototype» є всюди й важить мало,
    «lastIndexOf» є в одному місці й важить багато (idf);
  наскільки фрагмент довгий — довгий текст має більше шансів випадково зачепити
    слово запиту, тож його оцінка притискається (коефіцієнт b).

Від цього залежить, чи означає порівняння хоч щось. Показати, що вектори виграють у пошуку,
який рахує голі перетини слів, легко й нічого не доводить. Показати, що вони
виграють у BM25, — уже висновок: там, де запит і документ називають те саме
різними словами, підрахунок слів не рятує ніяка вага.

Токенізація навмисно проста: латиниця, цифри й підкреслення, усе в нижньому
регістрі. Специфікація англійська, стемінга немає — «objects» і «object»
лишаються різними словами. Це не недогляд: саме такий пошук стоїть за
замовчуванням у більшості проєктів, і саме з ним порівнюють вектори.
"""

import math
import re
from collections import Counter

from .corpus import Passage, load_passages

# BM25, класичні значення з літератури: k1 — наскільки швидко насичується
# повторення слова, b — наскільки сильно карається довжина фрагмента.
K1 = 1.5
B = 0.75

_TOKEN = re.compile(r"[a-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class LexicalIndex:
    """BM25 поверх усіх фрагментів. Будується за частки секунди, пам'яті майже не їсть."""

    def __init__(self, passages: list[Passage] | None = None):
        self.passages = passages if passages is not None else load_passages()
        # Заголовок підрозділу входить в індекс разом із текстом: назва
        # «String.prototype.replace» — найточніше слово, яким цей фрагмент можна знайти.
        docs = [tokenize(f"{p.heading}\n{p.text}") for p in self.passages]

        self.tf = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.avg_len = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0

        df = Counter()
        for counts in self.tf:
            df.update(counts.keys())
        n = len(docs)
        # Згладжений idf BM25. Слово, яке є в кожному фрагменті, отримує вагу
        # близько нуля, але не від'ємну — звідси +1 під логарифмом.
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def scores(self, query: str, k: int = 3) -> list[tuple[float, Passage]]:
        """Перші k фрагментів з оцінками, від більшої до меншої."""
        terms = tokenize(query)
        out = []
        for i, counts in enumerate(self.tf):
            norm = K1 * (1 - B + B * self.lengths[i] / (self.avg_len or 1))
            s = 0.0
            for t in terms:
                f = counts.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (K1 + 1) / (f + norm)
            if s > 0:
                out.append((s, self.passages[i]))
        out.sort(key=lambda x: -x[0])
        return out[:k]

    def retrieve(self, query: str, k: int = 3) -> list[Passage]:
        """Тільки фрагменти. Порожній список означає, що жодне слово запиту не збіглося."""
        return [p for _, p in self.scores(query, k)]
