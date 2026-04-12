// 1. 현재 유튜브 시간을 파이썬으로 보내는 역할 (기존과 동일)
setInterval(() => {
    const video = document.querySelector('video');
    if (video) {
        fetch('http://localhost:5000/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ time: video.currentTime })
        }).catch(err => {});
    }
}, 100);

// 2. 💡 파이썬에서 날아온 '시간 변경(SEEK)' 명령을 받아오는 역할 (새로 추가됨)
setInterval(() => {
    const video = document.querySelector('video');
    if (video) {
        fetch('http://localhost:5000/get_command')
        .then(res => res.json())
        .then(data => {
            // 서버에 명령이 있고, 그게 seek 명령이라면?
            if (data.command === 'seek' && data.time !== undefined) {
                console.log("🎬 라즈베리 파이 컨트롤러 시간 점프:", data.time);
                video.currentTime = data.time; // 유튜브 영상 시간 강제 이동!
            }
        }).catch(err => {});
    }
}, 500);

setInterval(() => {
    const video = document.querySelector('video');
    if (!video) return;

    fetch('http://localhost:5000/get_command')
    .then(res => res.json())
    .then(data => {
        if (!data || !data.command) return;

        // 💡 시간 이동
        if (data.command === 'seek' && data.time !== undefined) {
            video.currentTime = data.time;
        }
        // 💡 재생 및 일시정지
        else if (data.command === 'play') {
            video.play();
        }
        else if (data.command === 'pause') {
            video.pause();
        }
        // 💡 소리 조절 (유튜브 볼륨은 0.0 ~ 1.0 사이 값임)
        else if (data.command === 'volume' && data.value !== undefined) {
            video.volume = data.value / 100.0;
        }
    }).catch(() => {});
}, 500);