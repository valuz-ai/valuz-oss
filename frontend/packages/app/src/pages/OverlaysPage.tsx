import { useState } from 'react'
import {
  ShieldAlert,
  Terminal,
  FilePlus,
  AlertTriangle,
  CheckCircle2,
  Info,
  XCircle,
  Search,
} from 'lucide-react'
import {
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  CommandPalette,
  PermissionRequestDialog,
} from '@valuz/ui'
import { toast } from 'sonner'

export const OverlaysPage = () => {
  const [cmdkOpen, setCmdkOpen] = useState(false)
  const [permOpen, setPermOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [execOpen, setExecOpen] = useState(false)
  const [fileWriteOpen, setFileWriteOpen] = useState(false)

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[780px] space-y-10 px-10 py-10">
        <div>
          <h1 className="mb-2 font-heading text-2xl font-semibold text-ink-heading">浮层与弹窗</h1>
          <p className="max-w-[540px] text-base leading-relaxed text-ink-body">
            应用中所有浮层元素的集合：命令面板、权限确认弹窗、确认对话和 Toast 通知。点击下方按钮可交互预览。
          </p>
        </div>

        {/* Command palette */}
        <section>
          <h2 className="label-mono mb-3 font-heading">命令面板（⌘K）</h2>
          <Card>
            <CardContent className="flex items-start gap-4 py-5">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-light text-brand">
                <Search className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-1 text-sm font-medium text-ink-heading">快速命令面板</div>
                <p className="mb-3 text-sm leading-relaxed text-ink-body">
                  按下 ⌘K 唤起面板，搜索命令、技能、工作空间快速跳转。
                </p>
                <Button variant="outline" size="sm" onClick={() => setCmdkOpen(true)}>
                  打开命令面板
                </Button>
              </div>
            </CardContent>
          </Card>
        </section>

        {/* Permission dialogs */}
        <section>
          <h2 className="label-mono mb-3 font-heading">权限确认</h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {[
              { icon: <ShieldAlert className="h-4 w-4" />, title: '文件访问', desc: 'Skill 请求读取本地文件', onClick: () => setPermOpen(true) },
              { icon: <Terminal className="h-4 w-4" />, title: '执行命令', desc: 'Agent 请求运行 bash', onClick: () => setExecOpen(true) },
              { icon: <FilePlus className="h-4 w-4" />, title: '文件写入', desc: 'Agent 请求写入文件', onClick: () => setFileWriteOpen(true) },
            ].map((p) => (
              <Card
                key={p.title}
                className="transition-all duration-150 hover:border-surface-border-hover hover:shadow-sm"
                onClick={p.onClick}
              >
                <CardContent className="py-4">
                  <div className="mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-brand-light text-brand">
                    {p.icon}
                  </div>
                  <div className="mb-0.5 text-sm font-medium text-ink-heading">{p.title}</div>
                  <div className="text-xs text-ink-body">{p.desc}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>

        {/* Confirm */}
        <section>
          <h2 className="label-mono mb-3 font-heading">确认对话</h2>
          <Button variant="outline" size="sm" onClick={() => setConfirmOpen(true)}>
            删除项目
          </Button>
        </section>

        {/* Toasts */}
        <section>
          <h2 className="label-mono mb-3 font-heading">Toast 通知</h2>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => toast.success('操作成功', { description: '会话已保存到项目' })}>
              <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
              Success
            </Button>
            <Button variant="outline" size="sm" onClick={() => toast.info('提示', { description: 'Tesla 年度报告索引中' })}>
              <Info className="mr-1.5 h-3.5 w-3.5" />
              Info
            </Button>
            <Button variant="outline" size="sm" onClick={() => toast.warning('警告', { description: '连接超时，请检查网络' })}>
              <AlertTriangle className="mr-1.5 h-3.5 w-3.5" />
              Warning
            </Button>
            <Button variant="outline" size="sm" onClick={() => toast.error('错误', { description: 'API Key 无效，请重新配置' })}>
              <XCircle className="mr-1.5 h-3.5 w-3.5" />
              Error
            </Button>
          </div>
        </section>
      </div>

      {/* Command palette overlay */}
      <CommandPalette open={cmdkOpen} onOpenChange={setCmdkOpen} />

      {/* Permission dialog — File access */}
      <PermissionRequestDialog
        type="file-access"
        open={permOpen}
        onOpenChange={setPermOpen}
        path="~/Downloads/nvda-q4.csv"
        onAllow={() => { setPermOpen(false); toast.success('已允许文件访问') }}
        onDeny={() => { setPermOpen(false); toast.error('已拒绝文件访问') }}
      />

      {/* Permission dialog — Execute command */}
      <PermissionRequestDialog
        type="execute"
        open={execOpen}
        onOpenChange={setExecOpen}
        command="$ python3 analyze.py --input nvda-q4.csv"
        onAllow={() => { setExecOpen(false); toast.success('命令执行完成') }}
        onDeny={() => { setExecOpen(false); toast.error('已拒绝命令执行') }}
      />

      {/* Permission dialog — File write */}
      <PermissionRequestDialog
        type="file-write"
        open={fileWriteOpen}
        onOpenChange={setFileWriteOpen}
        path="~/Documents/nvda-q4-report.md"
        onAllow={() => { setFileWriteOpen(false); toast.success('文件写入完成') }}
        onDeny={() => { setFileWriteOpen(false); toast.error('已拒绝文件写入') }}
      />

      {/* Confirm dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除项目？</DialogTitle>
            <DialogDescription>
              删除「英伟达 2025 深度研究」将永久移除该项目下所有会话历史、生成文件与指示词配置。此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>取消</Button>
            <Button variant="destructive" size="sm" onClick={() => { setConfirmOpen(false); toast.success('项目已删除') }}>确认删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
