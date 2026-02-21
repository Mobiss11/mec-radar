# Правила фронтенда и дизайна

Этот файл — обязательные constraints для генерации фронтенд-кода.
Цель: UI дизайнерского уровня, а не "AI-слоп".

## Типографика
- NEVER используй Inter, Roboto, Arial, system-ui как основной шрифт
- ALWAYS выбирай уникальные шрифты: Clash Display, Satoshi, Playfair Display,
  Crimson Pro, IBM Plex Sans, JetBrains Mono, Space Grotesk, Cabinet Grotesk
- Экстремальные контрасты жирности: 200 vs 800
- Размеры заголовков: прыжки 3x+ (например 14px body → 48px h1)
- line-height: 1.5 для body, 1.1-1.2 для заголовков
- letter-spacing: отрицательный для крупных заголовков (-0.02em)

## Цвет и тема
- Доминантный цвет с резкими акцентами — не равномерное распределение
- CSS-переменные для всех цветов (совместимость с темами Telegram)
- NEVER фиолетовые градиенты на белом (AI-слоп)
- Поддержка тёмной и светлой темы Telegram через CSS-переменные
- Каждый раз меняй эстетику — не повторяй один стиль

## Фоны и атмосфера
- NEVER используй сплошной белый/серый фон
- ALWAYS создавай глубину: CSS-градиенты, геометрические паттерны, текстуры
- Слои: background → surface → content → overlay

## Компоненты
- shadcn/ui как база — NEVER модифицируй файлы в components/ui/
- Композиция из примитивов в components/common/
- Каждый компонент принимает className prop
- cn() (clsx + twMerge) для условных классов
- Forwardref для всех интерактивных компонентов
- Variants через cva (class-variance-authority)

## Анимации
- Framer Motion для orchestrated анимаций
- Staggered reveals при загрузке страницы — больше эффекта чем микро-анимации
- ALWAYS уважай prefers-reduced-motion
- Transition для hover/focus: 150-200ms ease-out
- NEVER анимируй width/height — используй transform: scale()

## Адаптивность (Mobile-First)
- Telegram Mini Apps = мобилка: начинай с 375px
- Breakpoints: sm(640) md(768) lg(1024) xl(1280)
- Touch targets: минимум 44x44px
- Отступы: p-4 (мобилка) → p-6 (планшет) → p-8 (десктоп)
- Тестируй на 375px, 768px, 1024px (Playwright скриншоты)

## Доступность (WCAG AA)
- Контраст: 4.5:1 для обычного текста, 3:1 для крупного (18px+ bold)
- Semantic HTML: nav, main, article, section, aside, button (не div с onClick)
- Клавиатурная навигация: все интерактивные элементы focusable
- aria-label для иконок без текста
- Focus-visible стили (outline, ring)

## Состояния
- Skeleton loading — NEVER спиннеры
- Empty state с иллюстрацией и CTA
- Error state с понятным сообщением и действием
- Hover, focus, active, disabled для всех интерактивных элементов

## Telegram Mini Apps
- @tma.js/sdk для TWA bridge (не window.Telegram.WebApp)
- BackButton API для навигации
- HapticFeedback на ключевых действиях
- MainButton для primary action
- Поддержка expansion (viewport height)
- Safe area insets для iPhone notch
