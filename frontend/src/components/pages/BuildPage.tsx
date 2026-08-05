"use client"
import { type CSSProperties, type ReactNode, useEffect, useState } from "react"

// Types

type QuestionType = "single" | "multi"

interface Question {
  id: string
  label: string
  type: QuestionType
  options: string[]
}

interface UseCase {
  label: string
  icon: string
  description: string
  questions: Question[]
}

type UseCaseKey =
  | "gaming"
  | "productivity"
  | "creative"
  | "streaming"
  | "aiml"
  | "nas"

interface UseCasePreferences {
  category: UseCaseKey
  label: string
  preferences: Record<string, string | string[]>
}

export interface BuildConfiguratorPayload {
  useCases: UseCasePreferences[]
  submittedAt: string
}

interface BuildConfiguratorProps {
  onComplete?: (payload: BuildConfiguratorPayload) => void
}

type Step = "select" | "configure" | "done"

type Answers = Record<string, string | string[]>

// Data

export const USE_CASES: Record<UseCaseKey, UseCase> = {
  gaming: {
    label: "Gaming",
    icon: "🎮",
    description: "Play PC games at your desired quality",
    questions: [
      {
        id: "resolution",
        label: "What resolution will you play at?",
        type: "single",
        options: [
          "1080p (Full HD)",
          "1440p (Quad HD)",
          "4K (Ultra HD)",
          "Ultrawide (3440×1440)",
        ],
      },
      {
        id: "fps",
        label: "What frame rate are you targeting?",
        type: "single",
        options: [
          "60 fps (Smooth)",
          "120 fps (High Refresh)",
          "144+ fps (Competitive)",
          "Uncapped / As high as possible",
        ],
      },
      {
        id: "gameTypes",
        label: "What types of games do you play?",
        type: "multi",
        options: [
          "AAA Open World (Cyberpunk, Elden Ring)",
          "Competitive FPS (Valorant, CS2)",
          "Strategy / Simulation (Civ, Cities)",
          "MMOs (WoW, FF14)",
          "Indie / Retro",
          "VR Gaming",
        ],
      },
      {
        id: "raytracing",
        label: "Is ray tracing important to you?",
        type: "single",
        options: ["Yes, must have", "Nice to have", "Don't care"],
      },
    ],
  },
  productivity: {
    label: "Productivity",
    icon: "💼",
    description: "Office work, browsing, and multitasking",
    questions: [
      {
        id: "monitors",
        label: "How many monitors will you use?",
        type: "single",
        options: [
          "Single monitor",
          "Dual monitors",
          "Triple or more",
          "Not sure yet",
        ],
      },
      {
        id: "tasks",
        label: "What will you primarily do?",
        type: "multi",
        options: [
          "Web browsing with many tabs",
          "Office suite (Word, Excel, etc.)",
          "Email & communication",
          "Light photo editing",
          "Spreadsheets & data work",
        ],
      },
      {
        id: "storage",
        label: "How much storage do you need?",
        type: "single",
        options: [
          "256 GB (minimal)",
          "512 GB (moderate)",
          "1 TB (comfortable)",
          "2 TB+ (extensive)",
        ],
      },
    ],
  },
  creative: {
    label: "Creative Work",
    icon: "🎨",
    description: "Video editing, 3D rendering, design",
    questions: [
      {
        id: "software",
        label: "Which software do you use?",
        type: "multi",
        options: [
          "Adobe Premiere / After Effects",
          "DaVinci Resolve",
          "Blender / Maya / 3ds Max",
          "Photoshop / Lightroom",
          "Figma / Sketch / Illustrator",
          "Unreal Engine / Unity",
        ],
      },
      {
        id: "videoRes",
        label: "What resolution do you edit or render at?",
        type: "single",
        options: ["1080p", "4K", "6K / 8K", "Not applicable"],
      },
      {
        id: "ram",
        label: "How memory-intensive is your workflow?",
        type: "single",
        options: [
          "Light (single project, small files)",
          "Medium (multiple apps, moderate files)",
          "Heavy (huge timelines, complex scenes)",
          "Extreme (8K footage, massive 3D scenes)",
        ],
      },
      {
        id: "colorAccuracy",
        label: "Do you need color-accurate output?",
        type: "single",
        options: [
          "Yes, color-critical work",
          "Somewhat important",
          "Not important",
        ],
      },
    ],
  },
  streaming: {
    label: "Streaming",
    icon: "📡",
    description: "Live streaming and content creation",
    questions: [
      {
        id: "platform",
        label: "Where do you stream?",
        type: "multi",
        options: [
          "Twitch",
          "YouTube",
          "Kick",
          "TikTok Live",
          "Other / Multiple",
        ],
      },
      {
        id: "streamQuality",
        label: "What stream quality are you targeting?",
        type: "single",
        options: ["720p 30fps", "1080p 30fps", "1080p 60fps", "4K streaming"],
      },
      {
        id: "encoding",
        label: "Preferred encoding approach?",
        type: "single",
        options: [
          "GPU encoding (NVENC / AMF)",
          "CPU encoding (x264)",
          "Dedicated capture card",
          "Not sure / recommend for me",
        ],
      },
      {
        id: "multitask",
        label: "Will you game while streaming?",
        type: "single",
        options: ["Yes, always", "Sometimes", "No, stream-only content"],
      },
    ],
  },
  aiml: {
    label: "AI / Machine Learning",
    icon: "🧠",
    description: "Training models and running inference",
    questions: [
      {
        id: "workload",
        label: "What's your primary AI workload?",
        type: "single",
        options: [
          "Training large models",
          "Fine-tuning existing models",
          "Inference / running models",
          "Experimenting / learning",
        ],
      },
      {
        id: "frameworks",
        label: "Which frameworks do you use?",
        type: "multi",
        options: [
          "PyTorch",
          "TensorFlow",
          "JAX",
          "Hugging Face",
          "ONNX",
          "Other",
        ],
      },
      {
        id: "vram",
        label: "How much VRAM do you expect to need?",
        type: "single",
        options: [
          "8–12 GB (small models)",
          "16–24 GB (medium models)",
          "48 GB+ (large models)",
          "Multi-GPU setup",
        ],
      },
    ],
  },
  nas: {
    label: "Home Server / NAS",
    icon: "🗄️",
    description: "Storage, media server, or home lab",
    questions: [
      {
        id: "purpose",
        label: "What will the server do?",
        type: "multi",
        options: [
          "File storage & backup",
          "Media server (Plex, Jellyfin)",
          "Home automation hub",
          "Docker containers / VMs",
          "Surveillance / security cameras",
        ],
      },
      {
        id: "capacity",
        label: "How much storage capacity?",
        type: "single",
        options: ["Under 4 TB", "4–12 TB", "12–50 TB", "50 TB+"],
      },
      {
        id: "redundancy",
        label: "What level of data protection?",
        type: "single",
        options: [
          "None (single drive)",
          "RAID 1 (mirror)",
          "RAID 5/6 (parity)",
          "ZFS / advanced",
        ],
      },
    ],
  },
}

// Sub-components

interface FadeInProps {
  children: ReactNode
  delay?: number
  className?: string
}

function FadeIn({ children, delay = 0, className = "" }: FadeInProps) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setVisible(true), delay)
    return () => clearTimeout(t)
  }, [delay])
  return (
    <div
      className={className}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(12px)",
        transition: "opacity 0.5s ease, transform 0.5s ease",
      }}
    >
      {children}
    </div>
  )
}

interface ProgressDotsProps {
  total: number
  current: number
}

function ProgressDots({ total, current }: ProgressDotsProps) {
  return (
    <div
      style={{
        display: "flex",
        gap: 6,
        justifyContent: "center",
        margin: "24px 0 8px",
      }}
    >
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          style={{
            width: i === current ? 24 : 8,
            height: 8,
            borderRadius: 4,
            background: i <= current ? "#d4a574" : "rgba(180,170,160,0.25)",
            transition: "all 0.3s ease",
          }}
        />
      ))}
    </div>
  )
}

interface CheckboxProps {
  checked: boolean
  onChange: () => void
  label: string
}

function Checkbox({ checked, onChange, label }: CheckboxProps) {
  return (
    <label
      className="build-option"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 16px",
        borderRadius: 10,
        border: checked
          ? "1.5px solid #d4a574"
          : "1.5px solid rgba(180,170,160,0.2)",
        background: checked ? "rgba(212,165,116,0.06)" : "transparent",
        cursor: "pointer",
        transition: "all 0.2s ease",
        userSelect: "none",
        position: "relative",
      }}
    >
      <input
        type="checkbox"
        className="build-option-input"
        checked={checked}
        onChange={onChange}
      />
      <div
        style={{
          width: 20,
          height: 20,
          borderRadius: 5,
          border: checked
            ? "2px solid #d4a574"
            : "2px solid rgba(180,170,160,0.35)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: checked ? "#d4a574" : "transparent",
          transition: "all 0.2s ease",
          flexShrink: 0,
        }}
      >
        {checked && (
          // Decorative: the checked state is already carried by the real
          // <input> above, so announcing the tick again would just be noise.
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M2.5 6L5 8.5L9.5 3.5"
              stroke="white"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </div>
      <span style={{ fontSize: 14.5, color: "#3d3529", lineHeight: 1.3 }}>
        {label}
      </span>
    </label>
  )
}

interface RadioOptionProps {
  selected: boolean
  onChange: () => void
  label: string
  /** Shared across one question's options so the browser groups them and
   *  arrow keys move between them. */
  name: string
}

function RadioOption({ selected, onChange, label, name }: RadioOptionProps) {
  return (
    <label
      className="build-option"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 16px",
        borderRadius: 10,
        border: selected
          ? "1.5px solid #d4a574"
          : "1.5px solid rgba(180,170,160,0.2)",
        background: selected ? "rgba(212,165,116,0.06)" : "transparent",
        cursor: "pointer",
        transition: "all 0.2s ease",
        userSelect: "none",
        position: "relative",
      }}
    >
      <input
        type="radio"
        className="build-option-input"
        name={name}
        checked={selected}
        onChange={onChange}
      />
      <div
        style={{
          width: 20,
          height: 20,
          borderRadius: 10,
          border: selected
            ? "2px solid #d4a574"
            : "2px solid rgba(180,170,160,0.35)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transition: "all 0.2s ease",
          flexShrink: 0,
        }}
      >
        {selected && (
          <div
            style={{
              width: 10,
              height: 10,
              borderRadius: 5,
              background: "#d4a574",
            }}
          />
        )}
      </div>
      <span style={{ fontSize: 14.5, color: "#3d3529", lineHeight: 1.3 }}>
        {label}
      </span>
    </label>
  )
}

// Main component

export default function BuildConfigurator({
  onComplete,
}: BuildConfiguratorProps = {}) {
  const [step, setStep] = useState<Step>("select")
  const [selectedUseCases, setSelectedUseCases] = useState<UseCaseKey[]>([])
  const [currentUseCaseIndex, setCurrentUseCaseIndex] = useState(0)
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0)
  const [answers, setAnswers] = useState<Answers>({})
  const [payload, setPayload] = useState<BuildConfiguratorPayload | null>(null)

  /**
   * Transforms the flat `answers` state into a structured JSON array
   * suitable for the LangChain recommendation pipeline.
   */
  const buildPayload = (): BuildConfiguratorPayload => {
    const useCases = selectedUseCases.map((key) => {
      const uc = USE_CASES[key]
      const preferences: Record<string, string | string[]> = {}
      for (const q of uc.questions) {
        const val = answers[`${key}.${q.id}`]
        if (val !== undefined) {
          preferences[q.id] = val
        }
      }
      return { category: key, label: uc.label, preferences }
    })
    return { useCases, submittedAt: new Date().toISOString() }
  }

  const toggleUseCase = (key: UseCaseKey) => {
    setSelectedUseCases((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    )
  }

  const currentUseCase = selectedUseCases[currentUseCaseIndex]
  const currentUseCaseData = USE_CASES[currentUseCase]
  const currentQuestion = currentUseCaseData?.questions?.[currentQuestionIndex]

  const totalQuestions = selectedUseCases.reduce(
    (sum, key) => sum + USE_CASES[key].questions.length,
    0,
  )
  const completedQuestions =
    selectedUseCases
      .slice(0, currentUseCaseIndex)
      .reduce((sum, key) => sum + USE_CASES[key].questions.length, 0) +
    currentQuestionIndex

  const setAnswer = (
    useCaseKey: string,
    questionId: string,
    value: string | string[],
  ) => {
    setAnswers((prev) => ({ ...prev, [`${useCaseKey}.${questionId}`]: value }))
  }

  const currentAnswerKey =
    currentUseCase && currentQuestion
      ? `${currentUseCase}.${currentQuestion.id}`
      : null
  const currentAnswer = currentAnswerKey ? answers[currentAnswerKey] : undefined

  const canProceed = (): boolean => {
    if (!currentQuestion) return false
    if (currentQuestion.type === "single") return !!currentAnswer
    if (currentQuestion.type === "multi")
      return Array.isArray(currentAnswer) && currentAnswer.length > 0
    return false
  }

  const handleNext = () => {
    if (currentQuestionIndex < currentUseCaseData.questions.length - 1) {
      setCurrentQuestionIndex((i) => i + 1)
    } else if (currentUseCaseIndex < selectedUseCases.length - 1) {
      setCurrentUseCaseIndex((i) => i + 1)
      setCurrentQuestionIndex(0)
    } else {
      const built = buildPayload()
      setPayload(built)
      onComplete?.(built)
      setStep("done")
    }
  }

  const handleBack = () => {
    if (currentQuestionIndex > 0) {
      setCurrentQuestionIndex((i) => i - 1)
    } else if (currentUseCaseIndex > 0) {
      setCurrentUseCaseIndex((i) => i - 1)
      const prevUseCase = selectedUseCases[currentUseCaseIndex - 1]
      setCurrentQuestionIndex(USE_CASES[prevUseCase].questions.length - 1)
    } else {
      setStep("select")
      setCurrentUseCaseIndex(0)
      setCurrentQuestionIndex(0)
    }
  }

  const handleStartOver = () => {
    setStep("select")
    setSelectedUseCases([])
    setCurrentUseCaseIndex(0)
    setCurrentQuestionIndex(0)
    setAnswers({})
    setPayload(null)
  }

  // Styles
  const pageStyle: CSSProperties = {
    minHeight: "100vh",
    background:
      "linear-gradient(168deg, #faf8f5 0%, #f3efe9 40%, #ede7df 100%)",
    fontFamily: "'Newsreader', 'Georgia', 'Times New Roman', serif",
    display: "flex",
    justifyContent: "center",
    alignItems: "flex-start",
    padding: "48px 20px",
  }

  const cardStyle: CSSProperties = {
    width: "100%",
    maxWidth: 580,
    background: "rgba(255,255,255,0.65)",
    backdropFilter: "blur(20px)",
    borderRadius: 20,
    border: "1px solid rgba(180,170,160,0.18)",
    boxShadow:
      "0 8px 40px rgba(120,100,80,0.06), 0 1px 3px rgba(120,100,80,0.04)",
    padding: "40px 36px",
  }

  const headingStyle: CSSProperties = {
    fontSize: 28,
    fontWeight: 500,
    color: "#2c2519",
    letterSpacing: "-0.02em",
    lineHeight: 1.25,
    margin: 0,
  }

  const subTextStyle: CSSProperties = {
    fontSize: 15,
    color: "#8a7e6e",
    lineHeight: 1.55,
    margin: "8px 0 0",
    fontFamily: "'DM Sans', 'Helvetica Neue', sans-serif",
  }

  const buttonPrimary: CSSProperties = {
    padding: "12px 28px",
    borderRadius: 10,
    border: "none",
    background: "#d4a574",
    color: "#fff",
    fontSize: 14.5,
    fontWeight: 600,
    fontFamily: "'DM Sans', 'Helvetica Neue', sans-serif",
    cursor: "pointer",
    transition: "all 0.2s ease",
    letterSpacing: "0.01em",
  }

  const buttonSecondary: CSSProperties = {
    padding: "12px 28px",
    borderRadius: 10,
    border: "1.5px solid rgba(180,170,160,0.3)",
    background: "transparent",
    color: "#6b5e4f",
    fontSize: 14.5,
    fontWeight: 500,
    fontFamily: "'DM Sans', 'Helvetica Neue', sans-serif",
    cursor: "pointer",
    transition: "all 0.2s ease",
  }

  // Render

  // Step 1: Select use cases
  if (step === "select") {
    return (
      <div style={pageStyle}>
        <link
          href="https://fonts.googleapis.com/css2?family=Newsreader:wght@400;500;600&family=DM+Sans:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
        <div style={cardStyle}>
          <FadeIn>
            <div style={{ textAlign: "center", marginBottom: 32 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  color: "#b8a88a",
                  fontFamily: "'DM Sans', sans-serif",
                  marginBottom: 12,
                }}
              >
                Build Configurator
              </div>
              <h1 style={headingStyle}>What will you use this PC for?</h1>
              <p style={subTextStyle}>
                Select all that apply. We'll ask follow-up questions for each.
              </p>
            </div>
          </FadeIn>

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {(Object.entries(USE_CASES) as [UseCaseKey, UseCase][]).map(
              ([key, val], i) => (
                <FadeIn key={key} delay={80 + i * 50}>
                  {/*
                    The label now wraps a real checkbox instead of carrying
                    onClick handlers on two inner divs. Those existed only
                    because the label had no control to drive; with one there,
                    the browser supplies the toggle, the tab stop, keyboard
                    activation and the checked state announcement.
                  */}
                  <label
                    className="build-option"
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 14,
                      padding: "16px 18px",
                      borderRadius: 12,
                      border: selectedUseCases.includes(key)
                        ? "1.5px solid #d4a574"
                        : "1.5px solid rgba(180,170,160,0.18)",
                      background: selectedUseCases.includes(key)
                        ? "rgba(212,165,116,0.07)"
                        : "rgba(255,255,255,0.5)",
                      cursor: "pointer",
                      transition: "all 0.25s ease",
                      userSelect: "none",
                      position: "relative",
                    }}
                  >
                    <input
                      type="checkbox"
                      className="build-option-input"
                      checked={selectedUseCases.includes(key)}
                      onChange={() => toggleUseCase(key)}
                    />
                    <div
                      style={{
                        width: 22,
                        height: 22,
                        borderRadius: 6,
                        border: selectedUseCases.includes(key)
                          ? "2px solid #d4a574"
                          : "2px solid rgba(180,170,160,0.3)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: selectedUseCases.includes(key)
                          ? "#d4a574"
                          : "transparent",
                        transition: "all 0.2s ease",
                        flexShrink: 0,
                      }}
                    >
                      {selectedUseCases.includes(key) && (
                        <svg
                          width="13"
                          height="13"
                          viewBox="0 0 12 12"
                          fill="none"
                          aria-hidden="true"
                        >
                          <path
                            d="M2.5 6L5 8.5L9.5 3.5"
                            stroke="white"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      )}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                        }}
                      >
                        <span style={{ fontSize: 18 }}>{val.icon}</span>
                        <span
                          style={{
                            fontSize: 15.5,
                            fontWeight: 500,
                            color: "#2c2519",
                            fontFamily: "'DM Sans', sans-serif",
                          }}
                        >
                          {val.label}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 13,
                          color: "#9a8e7e",
                          fontFamily: "'DM Sans', sans-serif",
                          marginTop: 3,
                          marginLeft: 26,
                        }}
                      >
                        {val.description}
                      </div>
                    </div>
                  </label>
                </FadeIn>
              ),
            )}
          </div>

          <FadeIn delay={500}>
            <div
              style={{
                marginTop: 28,
                display: "flex",
                justifyContent: "center",
              }}
            >
              <button
                type="button"
                style={{
                  ...buttonPrimary,
                  opacity: selectedUseCases.length === 0 ? 0.4 : 1,
                  pointerEvents:
                    selectedUseCases.length === 0 ? "none" : "auto",
                }}
                onClick={() => {
                  setCurrentUseCaseIndex(0)
                  setCurrentQuestionIndex(0)
                  setStep("configure")
                }}
              >
                Continue →
              </button>
            </div>
          </FadeIn>
        </div>
      </div>
    )
  }

  // Step 2: Configure each use case question-by-question
  if (step === "configure") {
    return (
      <div style={pageStyle}>
        <link
          href="https://fonts.googleapis.com/css2?family=Newsreader:wght@400;500;600&family=DM+Sans:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
        <div style={cardStyle}>
          {/* Category pill */}
          <FadeIn key={`cat-${currentUseCase}`}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 8,
              }}
            >
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "5px 12px",
                  borderRadius: 20,
                  background: "rgba(212,165,116,0.1)",
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: "#b8905e",
                  fontFamily: "'DM Sans', sans-serif",
                  letterSpacing: "0.02em",
                }}
              >
                <span>{currentUseCaseData.icon}</span>
                {currentUseCaseData.label}
              </div>
              <span
                style={{
                  fontSize: 12,
                  color: "#b0a494",
                  fontFamily: "'DM Sans', sans-serif",
                }}
              >
                {completedQuestions + 1} of {totalQuestions}
              </span>
            </div>
          </FadeIn>

          <ProgressDots total={totalQuestions} current={completedQuestions} />

          {/* Question */}
          <FadeIn key={`q-${currentUseCase}-${currentQuestion.id}`}>
            <div style={{ marginTop: 28, marginBottom: 24 }}>
              <h2 style={{ ...headingStyle, fontSize: 22 }}>
                {currentQuestion.label}
              </h2>
              {currentQuestion.type === "multi" && (
                <p style={{ ...subTextStyle, fontSize: 13.5, marginTop: 6 }}>
                  Select all that apply
                </p>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {currentQuestion.options.map((opt) => {
                if (currentQuestion.type === "single") {
                  return (
                    <RadioOption
                      key={opt}
                      label={opt}
                      name={`${currentUseCase}-${currentQuestion.id}`}
                      selected={currentAnswer === opt}
                      onChange={() =>
                        setAnswer(currentUseCase, currentQuestion.id, opt)
                      }
                    />
                  )
                }
                const arr = Array.isArray(currentAnswer) ? currentAnswer : []
                return (
                  <Checkbox
                    key={opt}
                    label={opt}
                    checked={arr.includes(opt)}
                    onChange={() => {
                      const newArr = arr.includes(opt)
                        ? arr.filter((x) => x !== opt)
                        : [...arr, opt]
                      setAnswer(currentUseCase, currentQuestion.id, newArr)
                    }}
                  />
                )
              })}
            </div>
          </FadeIn>

          {/* Navigation */}
          <div
            style={{
              marginTop: 32,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <button type="button" style={buttonSecondary} onClick={handleBack}>
              ← Back
            </button>
            <button
              type="button"
              style={{
                ...buttonPrimary,
                opacity: canProceed() ? 1 : 0.4,
                pointerEvents: canProceed() ? "auto" : "none",
              }}
              onClick={handleNext}
            >
              {currentUseCaseIndex === selectedUseCases.length - 1 &&
              currentQuestionIndex === currentUseCaseData.questions.length - 1
                ? "Finish"
                : "Next →"}
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Step 3: Thank you
  if (step === "done") {
    return (
      <div style={pageStyle}>
        <link
          href="https://fonts.googleapis.com/css2?family=Newsreader:wght@400;500;600&family=DM+Sans:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
        <div style={{ ...cardStyle, textAlign: "center" as const }}>
          <FadeIn>
            <div style={{ fontSize: 56, marginBottom: 16 }}>✓</div>
            <h1 style={{ ...headingStyle, fontSize: 26 }}>Thank you!</h1>
            <p
              style={{
                ...subTextStyle,
                fontSize: 15,
                marginTop: 10,
                maxWidth: 380,
                marginLeft: "auto",
                marginRight: "auto",
              }}
            >
              Your preferences have been recorded. We'll use these to craft the
              perfect build recommendation for you.
            </p>

            {/* Summary */}
            <div style={{ marginTop: 28, textAlign: "left" as const }}>
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  color: "#b8a88a",
                  fontFamily: "'DM Sans', sans-serif",
                  marginBottom: 12,
                }}
              >
                Your Selections
              </div>
              {selectedUseCases.map((key) => {
                const uc = USE_CASES[key]
                return (
                  <div
                    key={key}
                    style={{
                      padding: "14px 16px",
                      borderRadius: 10,
                      background: "rgba(212,165,116,0.05)",
                      border: "1px solid rgba(180,170,160,0.12)",
                      marginBottom: 8,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginBottom: 8,
                      }}
                    >
                      <span style={{ fontSize: 16 }}>{uc.icon}</span>
                      <span
                        style={{
                          fontSize: 14,
                          fontWeight: 600,
                          color: "#2c2519",
                          fontFamily: "'DM Sans', sans-serif",
                        }}
                      >
                        {uc.label}
                      </span>
                    </div>
                    {uc.questions.map((q) => {
                      const ans = answers[`${key}.${q.id}`]
                      if (!ans) return null
                      const display = Array.isArray(ans) ? ans.join(", ") : ans
                      return (
                        <div
                          key={q.id}
                          style={{
                            fontSize: 13,
                            color: "#6b5e4f",
                            fontFamily: "'DM Sans', sans-serif",
                            marginBottom: 4,
                            paddingLeft: 24,
                          }}
                        >
                          <span style={{ color: "#9a8e7e" }}>{q.label}</span>
                          <br />
                          <span style={{ fontWeight: 500, color: "#4a3f32" }}>
                            {display}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>

            <div style={{ marginTop: 28 }}>
              <button
                type="button"
                style={buttonSecondary}
                onClick={handleStartOver}
              >
                Start Over
              </button>
            </div>

            {/* Dev: JSON payload preview */}
            {payload && (
              <div style={{ marginTop: 28, textAlign: "left" as const }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 8,
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      letterSpacing: "0.1em",
                      textTransform: "uppercase",
                      color: "#b8a88a",
                      fontFamily: "'DM Sans', sans-serif",
                    }}
                  >
                    JSON Payload
                  </div>
                  <button
                    type="button"
                    style={{
                      ...buttonSecondary,
                      padding: "4px 12px",
                      fontSize: 12,
                      borderRadius: 6,
                    }}
                    onClick={() =>
                      navigator.clipboard.writeText(
                        JSON.stringify(payload, null, 2),
                      )
                    }
                  >
                    Copy
                  </button>
                </div>
                <pre
                  style={{
                    background: "rgba(44,37,25,0.04)",
                    border: "1px solid rgba(180,170,160,0.2)",
                    borderRadius: 10,
                    padding: "16px",
                    fontSize: 12,
                    fontFamily: "'SF Mono', 'Fira Code', monospace",
                    color: "#4a3f32",
                    overflow: "auto",
                    maxHeight: 300,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    lineHeight: 1.5,
                    margin: 0,
                  }}
                >
                  {JSON.stringify(payload, null, 2)}
                </pre>
              </div>
            )}
          </FadeIn>
        </div>
      </div>
    )
  }

  return null
}
