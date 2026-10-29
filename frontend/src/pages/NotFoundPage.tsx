import { Link, useLocation } from 'react-router-dom'

/** 404：刊物的“勘误”页——说明这一页不存在，并给出从目录回去的去处。 */
export default function NotFoundPage() {
  const { pathname } = useLocation()
  return (
    <div className="mg-page er-page--errata">
      <div className="mg-folio-line">
        <span>勘误</span>
        <span>{pathname}</span>
      </div>
      <div className="mg-rule-double" />
      <div className="mg-errata">
        <span className="mg-errata__num" aria-hidden="true">404</span>
        <div className="mg-errata__body">
          <h1 className="mg-headline">此页不存在</h1>
          <p className="mg-deck">你要找的页面可能已被移除，或者链接写错了。可以从这些地方继续：</p>
          <ul className="mg-index">
            <li><Link className="mg-index__link" to="/"><div><strong>首页</strong><span>上传论文、查看最近解析</span></div><em>→</em></Link></li>
            <li><Link className="mg-index__link" to="/library"><div><strong>论文库</strong><span>检索已入库的论文</span></div><em>→</em></Link></li>
            <li><Link className="mg-index__link" to="/explore"><div><strong>文献研究</strong><span>从一个问题出发调研</span></div><em>→</em></Link></li>
          </ul>
        </div>
      </div>
    </div>
  )
}
