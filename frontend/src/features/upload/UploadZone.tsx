/**
 * UploadZone 组件
 *
 * PDF 文件上传区域。
 *
 * Props:
 *   onUpload: (file: File) => void - 上传回调
 *   isUploading: boolean - 是否正在上传（禁用交互）
 *
 * 功能：
 *   - 拖拽上传区域（支持 drag & drop）
 *   - 点击选择文件（隐藏的 input[type=file]）
 *   - 文件类型校验：仅接受 .pdf
 *   - 文件大小提示：最大 50MB
 *   - 拖拽时高亮边框
 *   - 上传中显示加载状态
 *
 * 样式：
 *   - 虚线边框：border-2, border-dashed, border-gray-600
 *   - 拖拽高亮：border-blue-500, bg-blue-500/10
 *   - 居中图标 + 文字提示
 *   - hover 时边框变亮
 */
