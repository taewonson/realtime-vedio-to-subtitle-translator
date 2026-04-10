// 0.5초(500ms)마다 반복 실행
setInterval(() => {
    const video = document.querySelector('video');
    
    // 비디오가 존재하고, 재생 중일 때만 작동
    if (video && !video.paused) {
        const currentTime = video.currentTime;
        
        // 파이썬 로컬 서버(5000번 포트)로 시간 전송
        fetch('http://localhost:5000/sync', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ time: currentTime })
        }).catch(err => {
            // 파이썬 서버가 꺼져있을 때 발생하는 에러는 조용히 무시
        });
    }
}, 500);
