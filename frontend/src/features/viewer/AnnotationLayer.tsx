import type { Annotation } from '@/types'

interface AnnotationLayerProps {
  annotations: Annotation[]
  pageNumber: number
  onDelete: (id: number) => void
}

const HIGHLIGHT_OPACITY = '0.35'

export function AnnotationLayer({ annotations, pageNumber, onDelete }: AnnotationLayerProps) {
  const pageAnnotations = annotations.filter(a => a.page_number === pageNumber)

  if (pageAnnotations.length === 0) return null

  return (
    <div className="absolute inset-0 pointer-events-none z-[5]">
      {pageAnnotations.map(ann => {
        if (ann.annotation_type === 'highlight') {
          return (
            <div
              key={ann.id}
              className="absolute group pointer-events-auto cursor-pointer"
              style={{
                left: `${ann.x * 100}%`,
                top: `${ann.y * 100}%`,
                width: `${ann.width * 100}%`,
                height: `${ann.height * 100}%`,
                backgroundColor: ann.color,
                opacity: HIGHLIGHT_OPACITY,
                mixBlendMode: 'multiply',
              }}
              onClick={() => onDelete(ann.id)}
              title={`点击删除标注: ${ann.text.slice(0, 40)}`}
            >
              <span className="absolute -top-5 left-0 hidden group-hover:block text-[9px] px-1.5 py-0.5 rounded bg-gray-900 text-gray-200 whitespace-nowrap shadow-lg z-50">
                点击删除
              </span>
            </div>
          )
        }

        return (
          <div
            key={ann.id}
            className="absolute group pointer-events-auto cursor-pointer"
            style={{
              left: `${ann.x * 100}%`,
              top: `${ann.y * 100}%`,
              width: `${ann.width * 100}%`,
              height: `${ann.height * 100}%`,
            }}
            onClick={() => onDelete(ann.id)}
            title={`点击删除标注: ${ann.text.slice(0, 40)}`}
          >
            {ann.annotation_type === 'underline' && (
              <div className="absolute bottom-0 left-0 w-full" style={{ height: '2px', backgroundColor: ann.color }} />
            )}
            {ann.annotation_type === 'strikethrough' && (
              <div className="absolute left-0 w-full" style={{ top: '50%', height: '2px', backgroundColor: ann.color, transform: 'translateY(-50%)' }} />
            )}
            <span className="absolute -top-5 left-0 hidden group-hover:block text-[9px] px-1.5 py-0.5 rounded bg-gray-900 text-gray-200 whitespace-nowrap shadow-lg z-50">
              点击删除
            </span>
          </div>
        )
      })}
    </div>
  )
}
