import { useCallback, useEffect, useMemo, useState } from 'react'

import LineChart, { SERIES_COLORS } from './LineChart'
import type { ChartSeries } from './LineChart'
import {
  ApiError,
  getBacktestTop5,
  getBacktestTop5Leaderboard,
  getLatestMlResult,
  getMlLeaderboard,
  getNnLatest,
  getStocks,
  trainAllMlModels,
  trainMlModel,
  trainNnComparison,
} from './api'
import type {
  ComparisonReport,
  GenerationResult,
  Me,
  MlLeaderboardRow,
  MlModelKey,
  MlResult,
  PeriodKey,
  Stock,
  Top5Result,
  Top5Strategy,
  Top5Summary,
} from './api'

// ml_selection.MODEL_REGISTRY 와 같은 키·순서
const ML_MODELS: { value: MlModelKey; label: string }[] = [
  { value: 'random_forest', label: 'RandomForest' },
  { value: 'extra_trees', label: 'ExtraTrees' },
  { value: 'gradient_boosting', label: 'GradientBoosting' },
  { value: 'ridge', label: 'Ridge(선형)' },
]

// Week 8 최종 비교칸은 collection.DEFAULT_FULL_START(수집 시작일)와 맞춰 항상
// 2020-01-01 ~ 지금까지 전체 기간으로 봅니다 — 위 기간·날짜 필터를 바꿔도 안 흔들립니다
const FULL_HISTORY_START = '2020-01-01'
// 저장된 모델의 trained_from 이 이보다 늦으면(예: 1년치로만 학습) "2020~2026 전체로
// 학습됨"으로 인정하지 않습니다 — 거래일 캘린더 때문에 정확히 01-01 캔들은 없을 수
// 있어 2주 정도 여유를 둡니다. ISO 날짜 문자열은 그대로 사전식 비교가 됩니다
const FULL_HISTORY_THRESHOLD = '2020-01-15'
// 비머신러닝 8대 전략과 공정 비교하려면 여기도 정식 채택 모델 하나로 고정합니다
const FINAL_ML_MODEL: MlModelKey = 'random_forest'

type Props = {
  me: Me
  onSessionLost: () => void
}

const STRATEGIES: { value: Top5Strategy; label: string; hint: string }[] = [
  { value: 'momentum', label: '모멘텀 (고가마감)', hint: 'Score = (종가-저가) / (고가-저가)' },
  { value: 'mean_reversion', label: '역추세 (낙폭과대)', hint: 'Score = -(종가/시가 - 1)' },
  { value: 'volume_surge', label: '거래량폭발', hint: 'Score = (거래량/20일평균) · I(양봉)' },
  { value: 'rolling_edge', label: '롤링엣지 (야간샤프)', hint: 'Score = 20일 밤수익률 평균/표준편차' },
  {
    value: 'factor_combo',
    label: '팩터콤보 (정배열+수급+야간샤프)',
    hint: '필터: 20일선 위 · 양봉 · 윗꼬리 30%↓ 만 통과 · Score = (거래량/20일평균) × 20일 야간샤프',
  },
  {
    value: 'vol_filtered_edge',
    label: '저변동 트렌드엣지 (안정형)',
    hint: '필터: 20일선 위 · 20일 밤변동성 2%↓ 만 통과 · Score = 20일 밤수익률 평균/표준편차 (MDD↓ · 샤프↑ 지향)',
  },
  {
    value: 'vol_surge_trend',
    label: '필터드 거래량폭발 (추세+저변동)',
    hint: '필터: 20일선 위 · 20일 밤변동성 2%↓ 만 통과 · Score = (거래량/20일평균) · I(양봉)',
  },
  {
    value: 'vol_surge_edge',
    label: '거래량폭발×야간샤프',
    hint: '필터 없음 · Score = (거래량/20일평균)·I(양봉) × 20일 야간샤프',
  },
]

const RANGES = [
  { days: 90, label: '3개월' },
  { days: 365, label: '1년' },
  { days: 1095, label: '3년' },
  { days: 1825, label: '5년' },
]

function pct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

/**
 * Top-K 전략 — 매일 유니버스에서 점수 상위 K종목을 골라 종가 배팅.
 *
 * 점수는 그날 자신의 데이터(모멘텀 · 역추세 · 거래량폭발) 또는 그 전까지의
 * 과거 밤수익률(롤링엣지)만 씁니다 — 서버(selection.py)가 미래의 값을
 * 들여다보지 않게 막아 둔 걸 그대로 보여줄 뿐입니다.
 */
type LeaderboardRow = { value: Top5Strategy; label: string; hint: string; k: number; summary: Top5Summary }

// 초과수익률 타깃은 손실이 1e-5 단위라 소수 넷째 자리로는 전부 0.0000 으로 보입니다
const fmtLoss = (v: number) => v.toExponential(2)

export default function Top5({ me, onSessionLost }: Props) {
  const [strategy, setStrategy] = useState<Top5Strategy>('momentum')
  const [days, setDays] = useState(365)
  const [k, setK] = useState(5)
  const [costPct, setCostPct] = useState(0.1) // 화면은 %, 서버는 소수(0.001)
  // 채우면 기간(days) 대신 이 구간으로 조회합니다 — 비우면 기존처럼 기간 선택을 씁니다
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [data, setData] = useState<Top5Result | null>(null)
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[] | null>(null)
  const [stocks, setStocks] = useState<Stock[]>([])
  const [error, setError] = useState<string | null>(null)
  // Week 8 최종 비교칸 — 정식 채택 모델(RandomForest)을 2020~현재 전체로 비교. 위
  // 기간·날짜 필터와 무관하게 항상 FULL_HISTORY_START 부터 봅니다(비용만 위 필터를 따름)
  const [finalBestNonMl, setFinalBestNonMl] = useState<LeaderboardRow | null>(null)
  const [finalMl, setFinalMl] = useState<MlResult | null>(null)
  const [finalStatus, setFinalStatus] = useState<string | null>(null)
  // 아래는 admin 용 실험 도구 — 위 기간·날짜 필터로 scikit-learn 모델 4종을 자유롭게 비교
  const [mlLeaderboard, setMlLeaderboard] = useState<MlLeaderboardRow[] | null>(null)
  const [mlStatus, setMlStatus] = useState<string | null>(null)
  const [mlTraining, setMlTraining] = useState(false)
  // 리더보드로 이긴 모델 하나만 골라 다른 기간(예: 2020~2026 전체)으로 재학습할 때 씁니다 —
  // 4종을 다 같이 돌리면 진 모델까지 똑같이 무거운 기간으로 재학습하는 낭비가 생깁니다
  const [selectedModel, setSelectedModel] = useState<MlModelKey>('random_forest')
  const [modelTraining, setModelTraining] = useState(false)

  // Week 10 — 룰베이스 최고 · 전통ML 최고 · 신경망 5종을 Train(IS)/Val/OOS 로 비교.
  // 날짜 경계는 서버 고정값(2020~2023/2024/2025~2026)이라 여기서 바꾸지 않습니다
  const [nnReport, setNnReport] = useState<ComparisonReport | null>(null)
  const [nnStatus, setNnStatus] = useState<string | null>(null)
  const [nnTraining, setNnTraining] = useState(false)
  const [nnShowArchitectures, setNnShowArchitectures] = useState(false)

  const handle = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.code === 'session_expired') {
        onSessionLost()
        return
      }
      setError(e instanceof ApiError ? e.message : (e as Error).message)
    },
    [onSessionLost],
  )

  useEffect(() => {
    getStocks().then(setStocks).catch(handle)
  }, [handle])

  const names = useMemo(() => new Map(stocks.map((s) => [s.ticker, s.name])), [stocks])

  const load = useCallback(async () => {
    try {
      setData(
        await getBacktestTop5(strategy, days, k, costPct / 100, {
          start: startDate || undefined,
          end: endDate || undefined,
        }),
      )
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [strategy, days, k, costPct, startDate, endDate, handle])

  // 처음 열렸을 때만 기본 필터로 한 번 불러오고, 그 뒤로는 "조회" 버튼을 눌러야 다시 불러옵니다
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 8대 전략을 같은 조건(기간 · 비용)으로 한 번에 비교하되, 종목 수(K)는 전략마다
  // 1~20 중 샤프지수가 가장 좋았던 값을 서버가 직접 찾아 돌려줍니다 — K 를 하나로
  // 고정하면 전략마다 유불리가 갈리기 때문입니다(블랙박스 금지 — 어떤 K 를 썼는지도 같이 보여줍니다).
  const loadLeaderboard = useCallback(async () => {
    try {
      const { rows } = await getBacktestTop5Leaderboard(days, costPct / 100, {
        start: startDate || undefined,
        end: endDate || undefined,
      })
      const byStrategy = new Map(rows.map((r) => [r.strategy, r]))
      setLeaderboard(
        STRATEGIES.flatMap((s) => {
          const row = byStrategy.get(s.value)
          return row ? [{ ...s, k: row.k, summary: row.summary }] : []
        }),
      )
    } catch (e) {
      handle(e)
    }
  }, [days, costPct, startDate, endDate, handle])

  useEffect(() => {
    void loadLeaderboard()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Week 8 최종 비교칸 — 8대 전략 중 샤프지수 최고와, 정식 채택 모델(RandomForest)을
  // 항상 2020-01-01 ~ 지금까지 전체 기간으로 비교합니다. 두 다리를 따로 불러와서,
  // 한쪽(아직 학습 안 된 모델)이 실패해도 다른 쪽은 그대로 보여줍니다
  const loadFinalComparison = useCallback(async () => {
    try {
      const { rows } = await getBacktestTop5Leaderboard(3650, costPct / 100, {
        start: FULL_HISTORY_START,
        end: undefined,
      })
      const best = [...rows].sort((a, b) => b.summary.sharpe - a.summary.sharpe)[0]
      const meta = best ? STRATEGIES.find((s) => s.value === best.strategy) : undefined
      setFinalBestNonMl(best && meta ? { ...meta, k: best.k, summary: best.summary } : null)
    } catch (e) {
      handle(e)
    }
    const notTrainedOnFullHistory =
      '아래 "머신러닝 모델 비교"에서 RandomForest를 고르고, 위 시작일을 2020-01-01로 비운 뒤 ' +
      '"선택 모델만 재학습"을 먼저 실행해 주세요(admin).'
    try {
      const ml = await getLatestMlResult(FINAL_ML_MODEL, 3650, costPct / 100, {
        start: FULL_HISTORY_START,
        end: undefined,
      })
      // load_and_score 는 "학습된 기간"과 무관하게 적용되므로, 지금 저장된 모델이
      // 실제로 2020년 근처부터 학습됐는지 직접 확인하지 않으면 1년치로만 학습된
      // 모델을 "2020~2026 학습"이라고 잘못 보여줄 수 있습니다
      if (ml.trained_from < FULL_HISTORY_THRESHOLD) {
        setFinalMl(ml)
        setFinalStatus(null)
      } else {
        setFinalMl(null)
        setFinalStatus(
          `저장된 RandomForest가 ${ml.trained_from.slice(0, 10)} ~ ${ml.trained_to.slice(0, 10)}로만 학습되어 있어 ` +
            `2020~2026 비교에 쓸 수 없습니다. ${notTrainedOnFullHistory}`,
        )
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === 'model_not_trained') {
        setFinalMl(null)
        setFinalStatus(`아직 학습된 RandomForest가 없습니다. ${notTrainedOnFullHistory}`)
        return
      }
      handle(e)
    }
  }, [costPct, handle])

  useEffect(() => {
    void loadFinalComparison()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 이미 학습된 모델들만 모아 재학습 없이 다시 채점합니다 — 아직 안 돌려본 모델은 빠집니다
  const loadMlLeaderboard = useCallback(async () => {
    try {
      const { rows } = await getMlLeaderboard(days, costPct / 100, {
        start: startDate || undefined,
        end: endDate || undefined,
      })
      setMlLeaderboard(rows)
      setMlStatus(
        rows.length === 0 ? '아직 학습된 머신러닝 모델이 없습니다. admin 이 먼저 전체 모델 재학습을 실행해야 합니다.' : null,
      )
    } catch (e) {
      handle(e)
    }
  }, [days, costPct, startDate, endDate, handle])

  useEffect(() => {
    void loadMlLeaderboard()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // "조회" 버튼 — 전략·기간·K·비용·날짜를 원하는 대로 다 맞춘 뒤 한 번에 다시 불러옵니다
  const [querying, setQuerying] = useState(false)
  const handleQuery = useCallback(async () => {
    setQuerying(true)
    try {
      await Promise.all([load(), loadLeaderboard(), loadMlLeaderboard(), loadFinalComparison()])
    } finally {
      setQuerying(false)
    }
  }, [load, loadLeaderboard, loadMlLeaderboard, loadFinalComparison])

  // 규칙 2: 구간 분할 없이 지금 선택한 기간 전체로 모델 4종을 전부 다시 학습합니다 (admin
  // 전용). RandomForest 도 다시 학습되므로 위 최종 비교칸도 같이 새로고침합니다
  const handleTrainAllMl = useCallback(async () => {
    setMlTraining(true)
    try {
      const { rows } = await trainAllMlModels(days, costPct / 100, {
        start: startDate || undefined,
        end: endDate || undefined,
      })
      setMlLeaderboard(rows)
      setMlStatus(null)
      await loadFinalComparison()
    } catch (e) {
      handle(e)
    } finally {
      setMlTraining(false)
    }
  }, [days, costPct, startDate, endDate, handle, loadFinalComparison])

  // 선택한 모델 하나만 지금 필터(기간 등)로 재학습 — 리더보드로 승자를 고른 뒤
  // 다른 기간(예: 시작일 2020-01-01 · 종료일 비움 = 2020~지금)으로 그 모델만
  // 다시 돌려볼 때 씁니다. RandomForest 를 다시 학습했다면 위 최종 비교칸도 바뀔 수
  // 있으니 같이 새로고침합니다
  const handleTrainOneMl = useCallback(async () => {
    setModelTraining(true)
    try {
      await trainMlModel(selectedModel, days, costPct / 100, {
        start: startDate || undefined,
        end: endDate || undefined,
      })
      await loadMlLeaderboard()
      if (selectedModel === FINAL_ML_MODEL) {
        await loadFinalComparison()
      }
    } catch (e) {
      handle(e)
    } finally {
      setModelTraining(false)
    }
  }, [selectedModel, days, costPct, startDate, endDate, handle, loadMlLeaderboard, loadFinalComparison])

  // 저장된 Week 10 비교 결과를 재계산 없이 불러옵니다 — 아직 한 번도 안 돌렸으면 안내만 띄웁니다
  const loadNn = useCallback(async () => {
    try {
      setNnReport(await getNnLatest())
      setNnStatus(null)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'model_not_trained') {
        setNnReport(null)
        setNnStatus('아직 Week 10 비교를 실행하지 않았습니다. admin 이 먼저 "전체 재학습"을 실행해야 합니다.')
        return
      }
      handle(e)
    }
  }, [handle])

  useEffect(() => {
    void loadNn()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 룰베이스 8종 + 전통ML 4종(Train 구간만 재학습) + 신경망 5종을 전부 다시 비교합니다 —
  // admin 전용, CPU 로 몇 분 걸릴 수 있습니다. 기간은 항상 전체(2020~현재)로 고정합니다
  const handleTrainNn = useCallback(async () => {
    setNnTraining(true)
    try {
      await trainNnComparison(3650, costPct / 100)
      // 재학습 응답에는 탐색 최고(search_winner)가 없어서, 저장된 결과를 다시 읽어 옵니다
      await loadNn()
    } catch (e) {
      handle(e)
    } finally {
      setNnTraining(false)
    }
  }, [costPct, handle, loadNn])

  const nnOosSeries: ChartSeries[] = useMemo(() => {
    if (!nnReport) return []
    const winner = nnReport.search_winner
    return [
      {
        key: 'rule',
        name: `룰베이스 최고 (${nnReport.rule_based.model_label})`,
        points: nnReport.rule_based.oos.points.map((p) => ({ t: p.time, v: p.cum_portfolio })),
      },
      {
        key: 'ml',
        name: `전통ML 최고 (${nnReport.traditional_ml.model_label})`,
        points: nnReport.traditional_ml.oos.points.map((p) => ({ t: p.time, v: p.cum_portfolio })),
      },
      {
        key: 'nn',
        name: `신경망 최고 (${nnReport.neural_net_best.model_label})`,
        points: nnReport.neural_net_best.oos.points.map((p) => ({ t: p.time, v: p.cum_portfolio })),
      },
      ...(winner
        ? [
            {
              key: 'search',
              name: `탐색 최고 (${winner.model_label})`,
              points: winner.oos.points.map((p) => ({ t: p.time, v: p.cum_portfolio })),
            },
          ]
        : []),
    ]
  }, [nnReport])

  // 탐색 최고(앙상블)의 loss — 구성원마다 가중치를 고른 epoch 의 값을 평균합니다
  const winnerLoss = useMemo(() => {
    const details = (nnReport?.search_winner?.member_details ?? []).filter(
      (m): m is typeof m & { train_loss: number; val_loss: number } => m.train_loss !== null && m.val_loss !== null,
    )
    if (details.length === 0) return null
    return {
      train: details.reduce((sum, m) => sum + m.train_loss, 0) / details.length,
      val: details.reduce((sum, m) => sum + m.val_loss, 0) / details.length,
    }
  }, [nnReport])

  // 앙상블의 파라미터 수는 "모델 하나 × 개수"로 적습니다 — 합계만 쓰면 단일 모델 행과
  // 나란히 놓였을 때 모델 하나가 그만큼 큰 것처럼 읽힙니다
  const winnerParams = useMemo(() => {
    const winner = nnReport?.search_winner
    if (!winner || winner.param_count === null) return null
    const counts = winner.member_details.map((m) => m.param_count)
    const total = winner.param_count.toLocaleString()
    if (counts.length > 1 && counts.every((c) => c === counts[0])) {
      return `${counts[0].toLocaleString()} × ${counts.length}개 (합 ${total})`
    }
    return counts.length > 1 ? `합 ${total} (${counts.length}개)` : total
  }, [nnReport])

  const PERIOD_LABELS: Record<PeriodKey, string> = { is: 'Train(IS)', val: 'Val', oos: 'OOS' }

  // 구간마다 룰베이스 · 전통ML · 신경망 대표, 그리고 탐색을 돌렸다면 탐색 최고까지 한 묶음입니다
  const nnGroups = useMemo(() => {
    if (!nnReport) return []
    const groups: { title: string; result: GenerationResult }[] = [
      { title: '룰베이스 최고', result: nnReport.rule_based },
      { title: '전통ML 최고', result: nnReport.traditional_ml },
      { title: '신경망 최고', result: nnReport.neural_net_best },
    ]
    if (nnReport.search_winner) groups.push({ title: '탐색 최고', result: nnReport.search_winner })
    return groups
  }, [nnReport])

  const nnRows = useMemo(
    () =>
      (['is', 'val', 'oos'] as const).flatMap((period) =>
        nnGroups.map((g) => ({
          period,
          title: g.title,
          label: g.result.model_label,
          k: g.result.k,
          summary: g.result[period].summary,
        })),
      ),
    [nnGroups],
  )

  const rankedLeaderboard = useMemo(
    () => (leaderboard ? [...leaderboard].sort((a, b) => b.summary.calmar - a.summary.calmar) : []),
    [leaderboard],
  )
  const benchmark = leaderboard?.[0]?.summary
  const rankedMl = useMemo(
    () => (mlLeaderboard ? [...mlLeaderboard].sort((a, b) => b.summary.sharpe - a.summary.sharpe) : []),
    [mlLeaderboard],
  )

  const series: ChartSeries[] = useMemo(() => {
    if (!data || data.points.length === 0) return []
    return [
      { key: 'portfolio', name: `Top-${k} 전략`, points: data.points.map((p) => ({ t: p.time, v: p.cum_portfolio })) },
      { key: 'universe', name: '유니버스 평균', points: data.points.map((p) => ({ t: p.time, v: p.cum_universe })) },
    ]
  }, [data, k])

  const latestPicks = data ? data.picks.slice(-5).reverse() : []
  const chosen = STRATEGIES.find((s) => s.value === strategy)!

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Top-K 전략</h2>
        <span className="hint">점수는 당일 15:20까지 알 수 있는 정보만 씁니다 · 미래참조 없음</span>
      </div>

      <div className="filters">
        <label className="field">
          <span>전략</span>
          <select value={strategy} onChange={(e) => setStrategy(e.target.value as Top5Strategy)}>
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>기간</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {RANGES.map((r) => (
              <option key={r.days} value={r.days}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>K (몇 종목)</span>
          <input
            type="number"
            min={1}
            max={20}
            value={k}
            onChange={(e) => setK(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
            style={{ width: 70 }}
          />
        </label>
        <label className="field">
          <span>왕복비용(%)</span>
          <input
            type="number"
            min={0}
            max={5}
            step={0.01}
            value={costPct}
            onChange={(e) => setCostPct(Math.min(5, Math.max(0, Number(e.target.value) || 0)))}
            style={{ width: 80 }}
          />
        </label>
        <label className="field">
          <span>시작일 (비우면 기간 선택 사용)</span>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="field">
          <span>종료일 (비우면 지금까지)</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <button className="primary" onClick={() => void handleQuery()} disabled={querying} style={{ alignSelf: 'flex-end' }}>
          {querying ? '조회 중…' : '조회'}
        </button>
        <div className="spacer" />
      </div>

      <div className="hint" style={{ marginBottom: 14 }}>
        {chosen.hint}
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      {data && data.points.length > 0 ? (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[0] }} />
                {chosen.label} 누적
              </div>
              <div className="tile-value">{pct(data.summary.final_portfolio)}</div>
              <div className="tile-time">승률 {(data.summary.win_rate * 100).toFixed(1)}%</div>
            </div>
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[1] }} />
                유니버스 평균
              </div>
              <div className="tile-value">{pct(data.summary.final_universe)}</div>
              <div className="tile-time">전략과 같은 기간 · 같은 비용 차감</div>
            </div>
            <div className="tile">
              <div className="tile-label">초과수익</div>
              <div className="tile-value">
                {pct(data.summary.final_portfolio - data.summary.final_universe)}
              </div>
              <div className="tile-time">거래 {data.summary.trading_nights}일 밤</div>
            </div>
            <div className="tile">
              <div className="tile-label">최대낙폭(MDD)</div>
              <div className="tile-value">{pct(data.summary.mdd)}</div>
              <div className="tile-time">고점 대비 최대 하락폭</div>
            </div>
            <div className="tile">
              <div className="tile-label">샤프지수</div>
              <div className="tile-value">{data.summary.sharpe.toFixed(2)}</div>
              <div className="tile-time">연율화, 흔들림 대비 수익</div>
            </div>
            <div className="tile">
              <div className="tile-label">칼마지수</div>
              <div className="tile-value">{data.summary.calmar.toFixed(2)}</div>
              <div className="tile-time">누적수익률 ÷ |MDD|</div>
            </div>
            <div className="tile">
              <div className="tile-label">CAGR</div>
              <div className="tile-value">{pct(data.summary.cagr)}</div>
              <div className="tile-time">연율화 복리수익률</div>
            </div>
          </div>

          <div className="chart-wrap" style={{ marginBottom: 20 }}>
            <LineChart series={series} unit="%" />
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>종배 대상일</th>
                  <th>고른 종목</th>
                </tr>
              </thead>
              <tbody>
                {latestPicks.length === 0 && (
                  <tr>
                    <td colSpan={2} className="empty">
                      선택 기록이 없습니다.
                    </td>
                  </tr>
                )}
                {latestPicks.map((p) => (
                  <tr key={p.time}>
                    <td className="mono">{new Date(p.time).toLocaleDateString('ko-KR')}</td>
                    <td>
                      {p.tickers.length === 0
                        ? '(이 날은 조건을 만족한 종목이 없었습니다)'
                        : p.tickers.map((t) => names.get(t) ?? t).join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="state">
          적재된 일봉이 모자랍니다. 「유니버스」탭에서 코스피 100을 먼저 채워 주세요.
        </div>
      )}

      {rankedLeaderboard.length > 0 && (
        <>
          <div className="panel-head" style={{ marginTop: 28 }}>
            <h3 style={{ margin: 0 }}>전략 비교 리더보드</h3>
            <span className="hint">위와 같은 기간 · 비용으로 8대 전략을 한 번에 비교 · 종목 수(K)는 전략마다 샤프지수가 가장 좋은 값(1~20) · 칼마지수 순</span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>전략 이름</th>
                  <th>종목 선택 기준</th>
                  <th>종목 수</th>
                  <th>누적수익률</th>
                  <th>CAGR</th>
                  <th>MDD</th>
                  <th>샤프</th>
                  <th>칼마</th>
                </tr>
              </thead>
              <tbody>
                {benchmark && (
                  <tr>
                    <td>KOSPI 100 동일가중</td>
                    <td>전체 종목 종배 (벤치마크)</td>
                    <td>전체</td>
                    <td className="mono">{pct(benchmark.final_universe)}</td>
                    <td className="mono">{pct(benchmark.universe_cagr)}</td>
                    <td className="mono">{pct(benchmark.universe_mdd)}</td>
                    <td className="mono">{benchmark.universe_sharpe.toFixed(2)}</td>
                    <td className="mono">{benchmark.universe_calmar.toFixed(2)}</td>
                  </tr>
                )}
                {rankedLeaderboard.map((row) => (
                  <tr key={row.value} style={row.value === strategy ? { fontWeight: 600 } : undefined}>
                    <td>{row.label}</td>
                    <td>{row.hint}</td>
                    <td>{row.k}</td>
                    <td className="mono">{pct(row.summary.final_portfolio)}</td>
                    <td className="mono">{pct(row.summary.cagr)}</td>
                    <td className="mono">{pct(row.summary.mdd)}</td>
                    <td className="mono">{row.summary.sharpe.toFixed(2)}</td>
                    <td className="mono">{row.summary.calmar.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="panel-head" style={{ marginTop: 28 }}>
        <h3 style={{ margin: 0 }}>Week 8 — 비머신러닝 최고 vs 머신러닝 (정식 채택: RandomForest)</h3>
        <span className="hint">
          2020-01-01부터 지금까지 전체 기간으로 비교합니다(위 기간·날짜 필터와 무관, 비용만 위 필터를 따름) ·
          8대 전략 중 샤프지수 최고와, scikit-learn 4종 비교 끝에 고른 RandomForest(전 구간 학습)를 비교
        </span>
      </div>

      {finalStatus && <div className="hint">{finalStatus}</div>}

      {finalBestNonMl && finalMl && (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>방식</th>
                  <th>종목 선택 기준</th>
                  <th>종목 수</th>
                  <th>누적수익률</th>
                  <th>CAGR</th>
                  <th>MDD</th>
                  <th>샤프 🏆</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{finalBestNonMl.label} (비ML 최고)</td>
                  <td>{finalBestNonMl.hint}</td>
                  <td>{finalBestNonMl.k}</td>
                  <td className="mono">{pct(finalBestNonMl.summary.final_portfolio)}</td>
                  <td className="mono">{pct(finalBestNonMl.summary.cagr)}</td>
                  <td className="mono">{pct(finalBestNonMl.summary.mdd)}</td>
                  <td className="mono">{finalBestNonMl.summary.sharpe.toFixed(2)}</td>
                </tr>
                <tr>
                  <td>머신러닝 ({finalMl.model_label})</td>
                  <td>예측한 다음날 밤수익률 순위</td>
                  <td>{finalMl.k}</td>
                  <td className="mono">{pct(finalMl.result.summary.final_portfolio)}</td>
                  <td className="mono">{pct(finalMl.result.summary.cagr)}</td>
                  <td className="mono">{pct(finalMl.result.summary.mdd)}</td>
                  <td className="mono">{finalMl.result.summary.sharpe.toFixed(2)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="hint" style={{ marginTop: 8 }}>
            학습 데이터 {finalMl.trained_from.slice(0, 10)} ~ {finalMl.trained_to.slice(0, 10)} (실행{' '}
            {new Date(finalMl.trained_at).toLocaleString('ko-KR')}) · 표본 {finalMl.rows.toLocaleString()}개 ·
            특성중요도{' '}
            {finalMl.feature_importances.map((f) => `${f.name} ${(f.importance * 100).toFixed(1)}%`).join(' · ')}
          </div>
        </>
      )}

      <div className="panel-head" style={{ marginTop: 28 }}>
        <h3 style={{ margin: 0 }}>머신러닝 모델 비교 (admin 실험용)</h3>
        <span className="hint">
          위 기간·비용 필터로 scikit-learn 모델 4종(RandomForest · ExtraTrees · GradientBoosting · Ridge(선형))을
          자유롭게 비교 · 위 "비교칸"은 항상 RandomForest · 2020~현재로 고정이라 여기서 바꿔도 영향 없음
        </span>
      </div>

      {me.role === 'admin' && (
        <div className="filters" style={{ marginBottom: 12 }}>
          <button className="primary" onClick={() => void handleTrainAllMl()} disabled={mlTraining || modelTraining}>
            {mlTraining ? '전체 모델 학습 중…' : '전체 모델 재학습 (4종, 위 기간 전체·분할 없음)'}
          </button>
          <div className="spacer" />
          <label className="field">
            <span>모델 하나만 재학습</span>
            <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value as MlModelKey)}>
              {ML_MODELS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => void handleTrainOneMl()} disabled={mlTraining || modelTraining}>
            {modelTraining ? '학습 중…' : '선택 모델만 재학습'}
          </button>
        </div>
      )}

      {me.role === 'admin' && (
        <div className="hint" style={{ marginBottom: 12 }}>
          여기서 모델을 비교해 승자를 고른 뒤, RandomForest 가 아닌 모델이 이겼다면 위 FINAL_ML_MODEL(코드,
          Top5.tsx)을 바꿔야 "비교칸"에 반영됩니다. 시작일·종료일을 2020-01-01 ~ (비움 = 지금까지)로 바꾸고
          "선택 모델만 재학습"을 누르면 그 모델만 2020~2026 전체로 다시 학습합니다.
        </div>
      )}

      {mlStatus && <div className="hint">{mlStatus}</div>}

      {rankedMl.length > 0 && (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>모델</th>
                  <th>종목 수</th>
                  <th>누적수익률</th>
                  <th>CAGR</th>
                  <th>MDD</th>
                  <th>샤프 🏆</th>
                </tr>
              </thead>
              <tbody>
                {rankedMl.map((row, i) => (
                  <tr key={row.model_key} style={i === 0 ? { fontWeight: 600 } : undefined}>
                    <td>
                      {row.model_label}
                      {row.model_key === FINAL_ML_MODEL ? ' (정식 채택)' : ''}
                      {i === 0 ? ' 🏆' : ''}
                    </td>
                    <td>{row.k}</td>
                    <td className="mono">{pct(row.summary.final_portfolio)}</td>
                    <td className="mono">{pct(row.summary.cagr)}</td>
                    <td className="mono">{pct(row.summary.mdd)}</td>
                    <td className="mono">{row.summary.sharpe.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rankedMl.map((row) => (
            <div key={row.model_key} className="hint" style={{ marginTop: 8 }}>
              <strong>{row.model_label}</strong> — 학습 데이터 {row.trained_from.slice(0, 10)} ~{' '}
              {row.trained_to.slice(0, 10)} · 표본 {row.rows.toLocaleString()}개 · 특성중요도{' '}
              {row.feature_importances.map((f) => `${f.name} ${(f.importance * 100).toFixed(1)}%`).join(' · ')}
            </div>
          ))}
        </>
      )}

      <div className="panel-head" style={{ marginTop: 28 }}>
        <h3 style={{ margin: 0 }}>Week 10 — 룰베이스 vs 전통ML vs 신경망 (Train(IS) / Val / OOS)</h3>
        <span className="hint">
          전원 통일 실험 규격: Train 2020~2023(IS) · Val 2024(얼리스토핑) · OOS 2025~2026 · Top-5 고정 · 왕복비용
          위 설정값 · 전통ML과 신경망은 같은 입력(확장 피처 19개를 날짜별 종목 간 순위로 변환)·같은 타깃으로 학습하고, 신경망은 MLP·1D-CNN·LSTM·GRU·Transformer 5종을 전부 학습해 Val 샤프 최고를 대표로 씁니다
        </span>
      </div>

      {me.role === 'admin' && (
        <div className="filters" style={{ marginBottom: 12 }}>
          <button className="primary" onClick={() => void handleTrainNn()} disabled={nnTraining}>
            {nnTraining ? '신경망 5종 + 전통ML 재학습 중… (몇 분 걸릴 수 있습니다)' : '전체 재학습 (룰베이스 + 전통ML + 신경망 5종)'}
          </button>
        </div>
      )}

      {nnStatus && <div className="hint">{nnStatus}</div>}

      {nnReport && (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>구간</th>
                  <th>방식</th>
                  <th>종목 수</th>
                  <th>누적수익률</th>
                  <th>CAGR</th>
                  <th>MDD</th>
                  <th>승률</th>
                  <th>샤프</th>
                </tr>
              </thead>
              <tbody>
                {nnRows.map((row, i) => (
                  <tr key={`${row.period}-${row.title}`} style={row.period === 'oos' ? { fontWeight: 600 } : undefined}>
                    {i % nnGroups.length === 0 && (
                      <td rowSpan={nnGroups.length} className="mono">
                        {PERIOD_LABELS[row.period]}
                      </td>
                    )}
                    <td>{`${row.title} (${row.label})`}</td>
                    <td>{row.k}</td>
                    <td className="mono">{pct(row.summary.final_portfolio)}</td>
                    <td className="mono">{pct(row.summary.cagr)}</td>
                    <td className="mono">{pct(row.summary.mdd)}</td>
                    <td className="mono">{(row.summary.win_rate * 100).toFixed(1)}%</td>
                    <td className="mono">{row.summary.sharpe.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {nnReport.search_winner && (
            <div className="hint" style={{ margin: '8px 0' }}>
              「탐색 최고」는 단일 설정 {nnReport.search_winner.configs_tried}개와 최종 후보{' '}
              {nnReport.search_winner.candidates_compared}개 중 Val 샤프가 가장 높은 것을 고른 결과입니다(구성원{' '}
              {nnReport.search_winner.members.length}개의 순위 평균). 많이 시험해 고른 만큼 Val 샤프는 부풀려져
              있고, OOS 는 이 모델 하나에 대해서만 마지막에 한 번 계산했습니다. 구성원 설정은 그 탐색에서 고른
              뒤, 학습 코드의 버그를 고쳐 같은 설정으로 다시 학습했습니다. 「전체 재학습」으로는 갱신되지
              않습니다.
            </div>
          )}

          <div className="hint" style={{ margin: '8px 0' }}>
            OOS(표본밖) 구간 누적수익률 — 학습·튜닝에 전혀 관여하지 않은 구간입니다
          </div>
          <div className="chart-wrap" style={{ marginBottom: 20 }}>
            <LineChart series={nnOosSeries} unit="%" />
          </div>

          <button onClick={() => setNnShowArchitectures((v) => !v)} style={{ marginBottom: 12 }}>
            {nnShowArchitectures ? '신경망 아키텍처 5종 상세 숨기기' : '신경망 아키텍처 5종 상세 보기'}
          </button>

          {nnShowArchitectures && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>아키텍처</th>
                    <th>계열</th>
                    <th>파라미터 수</th>
                    <th>IS 샤프</th>
                    <th>Val 샤프</th>
                    <th>OOS 샤프</th>
                    <th>마지막 epoch loss (train/val)</th>
                  </tr>
                </thead>
                <tbody>
                  {nnReport.neural_net_all.map((arch) => {
                    const lastLoss = arch.loss_curve[arch.loss_curve.length - 1]
                    const isBest = arch.model_key === nnReport.neural_net_best.model_key
                    return (
                      <tr key={arch.model_key} style={isBest ? { fontWeight: 600 } : undefined}>
                        <td>
                          {arch.model_label}
                          {isBest ? ' 🏆' : ''}
                        </td>
                        <td>{arch.family === 'A' ? '당일 스냅샷(2D)' : '20일 시퀀스(3D)'}</td>
                        <td>{arch.param_count.toLocaleString()}</td>
                        <td className="mono">{arch.is.summary.sharpe.toFixed(2)}</td>
                        <td className="mono">{arch.val.summary.sharpe.toFixed(2)}</td>
                        <td className="mono">{arch.oos.summary.sharpe.toFixed(2)}</td>
                        <td className="mono">
                          {lastLoss ? `${fmtLoss(lastLoss.train_loss)} / ${fmtLoss(lastLoss.val_loss)}` : '-'}
                        </td>
                      </tr>
                    )
                  })}
                  {/* 탐색 최고는 5종 기본 설정과 조건이 달라(19피처 · 설정 탐색 · 앙상블) 맨 아래에 따로 둡니다 */}
                  {nnReport.search_winner && (
                    <tr>
                      <td>{`탐색 최고 (${nnReport.search_winner.model_label})`}</td>
                      <td>{`구성원 ${nnReport.search_winner.members.length}개 순위 평균`}</td>
                      <td>
                        {winnerParams ?? '-'}
                      </td>
                      <td className="mono">{nnReport.search_winner.is.summary.sharpe.toFixed(2)}</td>
                      <td className="mono">{nnReport.search_winner.val.summary.sharpe.toFixed(2)}</td>
                      <td className="mono">{nnReport.search_winner.oos.summary.sharpe.toFixed(2)}</td>
                      <td className="mono">{winnerLoss ? `${fmtLoss(winnerLoss.train)} / ${fmtLoss(winnerLoss.val)} (평균)` : '-'}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              {nnReport.search_winner && (
                <div className="hint" style={{ margin: '8px 0' }}>
                  탐색 최고 구성원: {nnReport.search_winner.members.join(' · ')}
                  <br />
                  {nnReport.search_winner.member_details.length > 0 && (
                    <>
                      구성원별 선택 epoch / 학습한 epoch:{' '}
                      {nnReport.search_winner.member_details
                        .map((m) => `시드 ${m.seed} — ${m.best_epoch}/${m.epochs}`)
                        .join(' · ')}
                      . 탐색 최고의 loss 는 구성원마다 가중치를 고른 epoch 의 값을 평균한 것입니다.
                      <br />
                    </>
                  )}
                  이름은 아키텍처 | 피처 세트(extended = 19개) | hidden | dropout | weight decay | 학습률 | 시드 순입니다. 🏆 는
                  기본 설정 5종 중 대표입니다.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
