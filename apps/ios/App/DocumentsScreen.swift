import SwiftUI
import UniformTypeIdentifiers
import SharedCore

/// 本机知识库：端侧 RAG 的资料入口。
///
/// 上一版（Android）只有聊天页底部一个"最近 3 份"的小面板，且显示成 `doc-rag (1 段, 36B)`——
/// 既看不到全部文档，也不知道"段"是什么。这一版做成独立页面：
/// 完整列表 + 可读的体积单位 + 删除 + 空状态说明"为什么要导入"。
struct DocumentsScreen: View {

    @Environment(\.sekb) private var pal

    @ObservedObject var viewModel: SekbViewModel
    let onBack: () -> Void

    @State private var showImporter = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            VStack(spacing: 0) {
                SekbTopBar(title: "本机知识库",
                           subtitle: viewModel.state.documentSummary,
                           onBack: onBack) { EmptyView() }

                if viewModel.state.importing {
                    VStack(alignment: .leading, spacing: 6) {
                        ProgressView(value: 0.5).progressViewStyle(.linear)
                        Text("正在导入并切分…")
                            .font(.system(size: 12))
                            .foregroundColor(pal.textSecondary)
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 6)
                }

                if viewModel.state.documents.isEmpty {
                    emptyState
                } else {
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            ForEach(viewModel.state.documents, id: \.id) { doc in
                                documentCard(doc)
                            }
                        }
                        .padding(12)
                    }
                }
            }

            Button {
                showImporter = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "plus").font(.system(size: 15, weight: .semibold))
                    Text("导入文档").font(.system(size: 14, weight: .medium))
                }
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .background(pal.primary)
                .clipShape(Capsule())
                .shadow(color: Color.black.opacity(0.18), radius: 6, y: 2)
            }
            .buttonStyle(.plain)
            .padding(16)
        }
        .fileImporter(isPresented: $showImporter,
                      allowedContentTypes: [.plainText, .pdf, .json, .commaSeparatedText],
                      allowsMultipleSelection: true) { result in
            switch result {
            case let .success(urls):
                for url in urls { viewModel.importDocument(url: url) }
            case let .failure(error):
                viewModel.state.error = "选择文件失败：\(error.localizedDescription)"
            }
        }
        .onAppear { viewModel.refreshDocuments() }
    }

    private var emptyState: some View {
        VStack(spacing: 0) {
            Spacer()
            SekbCircleIcon(systemName: "doc.text", size: 56, iconSize: 26)
            Text("还没有本机资料")
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(pal.textPrimary)
                .padding(.top, 14)
            Text("导入 PDF 或文本后，助手就能只在本机检索它们来回答——这些内容不会被上传。")
                .font(.system(size: 12))
                .foregroundColor(pal.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(32)
    }

    private func documentCard(_ doc: DocumentInfo) -> some View {
        SekbCard {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(doc.name)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(pal.textPrimary)
                        .lineLimit(1)
                    // "段"是内部概念，但对用户有意义（检索的单位），所以保留并配一句解释
                    Text("\(doc.chunks) 个片段 · \(TimeFmt.humanSize(doc.sizeBytes))")
                        .font(.system(size: 11))
                        .foregroundColor(pal.textSecondary)
                }
                Spacer(minLength: 4)
                Button {
                    viewModel.deleteDocument(id: doc.id)
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 14))
                        .foregroundColor(pal.textSecondary)
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.plain)
                .help("删除 \(doc.name)")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
    }
}
