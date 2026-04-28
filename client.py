import tkinter as tk
from tkinter import messagebox
import requests

SERVER_URL = "http://127.0.0.1:5000"

class AuthApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LOGIN")
        self.root.geometry("300x300")

        tk.Label(root, text="LOGIN", font=('Arial', 16, 'bold')).pack(pady=20)

        tk.Label(root, text="아이디:").pack()
        self.ent_id = tk.Entry(root)
        self.ent_id.pack()

        tk.Label(root, text="비밀번호:").pack()
        self.ent_pw = tk.Entry(root, show="*")
        self.ent_pw.pack()

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=20)
        tk.Button(btn_frame, text="로그인", width=10, command=self.login).grid(row=0, column=0, padx=5)
        tk.Button(btn_frame, text="회원가입", width=10, command=self.open_register).grid(row=0, column=1, padx=5)

        self.btn_edit = tk.Button(root, text="정보 수정", state=tk.DISABLED, command=self.open_edit)
        self.btn_edit.pack()

    def login(self):
        
        payload = {"username": self.ent_id.get(), "password": self.ent_pw.get()}
        res = requests.post(f"{SERVER_URL}/login", json=payload)
        if res.status_code == 200:
            self.current_user = res.json()
            messagebox.showinfo("로그인", f"{self.current_user['username']}님 환영합니다.")
            self.btn_edit.config(state=tk.NORMAL)
        else:
            messagebox.showerror("실패", res.json()['msg'])

    def open_register(self):
        reg_win = tk.Toplevel(self.root)
        reg_win.title("회원가입")
        reg_win.geometry("250x300")
        
        tk.Label(reg_win, text="아이디:").pack()
        e_id = tk.Entry(reg_win); e_id.pack()
        tk.Label(reg_win, text="비밀번호:").pack()
        e_pw = tk.Entry(reg_win, show="*"); e_pw.pack()
        tk.Label(reg_win, text="이메일:").pack()
        e_em = tk.Entry(reg_win); e_em.pack()
        
        def submit():
            payload = {"username": e_id.get(), "password": e_pw.get(), "email": e_em.get()}
            if not payload['username'] or not payload['password'] or not payload['email']:
                messagebox.showerror("오류", "빈칸을 모두 채워주세요")
                return
            res = requests.post(f"{SERVER_URL}/register", json=payload)
            messagebox.showinfo("가입", res.json()['msg'])
            if res.status_code == 201: reg_win.destroy()
        
        tk.Button(reg_win, text="완료", command=submit).pack(pady=10)

    def open_edit(self):
        edit_win = tk.Toplevel(self.root)
        edit_win.title("회원정보 수정")
        edit_win.geometry("250x300")
        
        tk.Label(edit_win, text="현재 비번 확인:").pack()
        e_cp = tk.Entry(edit_win, show="*"); e_cp.pack()
        tk.Label(edit_win, text="새 이메일:").pack()
        e_em = tk.Entry(edit_win); e_em.insert(0, self.current_user['email']); e_em.pack()
        tk.Label(edit_win, text="새 비번(공백 시 유지):").pack()
        e_np = tk.Entry(edit_win, show="*"); e_np.pack()
        
        def submit():
            payload = {
                "username": self.current_user['username'],
                "current_password": e_cp.get(),
                "new_email": e_em.get(),
                "new_password": e_np.get()
            }
            res = requests.post(f"{SERVER_URL}/update", json=payload)
            messagebox.showinfo("수정", res.json()['msg'])
            if res.status_code == 200: edit_win.destroy()
            
        tk.Button(edit_win, text="수정 완료", command=submit).pack(pady=10)

if __name__ == "__main__":
    root = tk.Tk()
    app = AuthApp(root)
    root.mainloop()