import { create } from "zustand";

type MobileNavigationState = {
  open: boolean;
  setOpen: (open: boolean) => void;
};

export const useMobileNavigationStore = create<MobileNavigationState>()(
  (set) => ({
    open: false,
    setOpen: (open) => set({ open }),
  }),
);

type NoticeTone = "success" | "error" | "info";

export type Notice = {
  id: number;
  message: string;
  tone: NoticeTone;
};

type NoticeState = {
  notices: Notice[];
  notify: (message: string, tone?: NoticeTone) => void;
  dismiss: (id: number) => void;
};

let noticeId = 0;

export const useNoticeStore = create<NoticeState>()((set) => ({
  notices: [],
  notify: (message, tone = "info") => {
    const id = ++noticeId;
    set((state) => ({
      notices: [...state.notices.slice(-3), { id, message, tone }],
    }));
  },
  dismiss: (id) =>
    set((state) => ({
      notices: state.notices.filter((notice) => notice.id !== id),
    })),
}));
