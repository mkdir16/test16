import React, { useState, useEffect } from 'react';

const tg = window.Telegram.WebApp;

function App() {
    const [step, setStep] = useState(0);
    const [score, setScore] = useState(0);

    const questions = [
        { q: "Чему равно 2+2?", options: ["3", "4", "5"], correct: 1 },
        { q: "Столица Узбекистана?", options: ["Самарканд", "Ташкент"], correct: 1 }
    ];

    useEffect(() => {
        tg.ready(); // Сообщаем TG, что приложение загружено
    }, []);

    const handleAnswer = (index) => {
        let newScore = score;
        if (index === questions[step].correct) {
            newScore += 1;
            setScore(newScore);
        }

        if (step + 1 < questions.length) {
            setStep(step + 1);
        } else {
            // Отправляем результат боту через sendData
            tg.sendData(JSON.stringify({ result: newScore, total: questions.length }));
        }
    };

    return (
        <div style={{ padding: '20px', color: 'var(--tg-theme-text-color)' }}>
            <h2>Вопрос {step + 1}</h2>
            <p>{questions[step].q}</p>
            {questions[step].options.map((opt, i) => (
                <button 
                    key={i} 
                    onClick={() => handleAnswer(i)}
                    style={{ display: 'block', margin: '10px 0', width: '100%', padding: '10px' }}
                >
                    {opt}
                </button>
            ))}
        </div>
    );
}

export default App;