import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* ErrorBoundary 必须包在最外层：
        渲染期未捕获的异常会让 React 卸载整棵树，而窗口是 transparent 的，
        没有兜底就是一片纯黑 —— 用户完全不知道该做什么。
        （主上实测踩过：「点了加装直接变黑，没进度条没提示没界面」） */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
