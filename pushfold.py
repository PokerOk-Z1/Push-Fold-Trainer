#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pushfold.py — консольный тренажёр пуш/фолд для турниров PokerOK (GG Network)
"""

import argparse
import csv
import json
import random
import itertools
import math
import statistics
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional, Literal

# ---------------------------
# === Структуры данных ===
# ---------------------------

@dataclass
class Spot:
    stacks_bb: float
    position: str
    players_left: int
    action_before: str
    sb: float
    bb: float
    ante: float
    bb_ante: bool
    pko: bool
    bounty_self: float
    bounty_op: float
    coverage: str  # self/op/full/none


@dataclass
class Advice:
    hand: str
    decision_chart: str
    ev_push: float
    ev_fold: float
    decision_ev: str
    final: str
    notes: Dict


# ---------------------------
# === Утилиты ===
# ---------------------------

def parse_hand_range(range_str: str) -> List[str]:
    """
    Простейший парсер диапазона рук (PokerStove/Equilab формат)
    Поддерживает: 22+, A2s+, ATo+, KQo, QTs, и т.п.
    """
    hands = []
    ranks = "AKQJT98765432"
    suited = ['s', 'o']
    for token in range_str.replace(" ", "").split(","):
        if not token:
            continue
        if '+' in token and len(token) == 3:  # пример: A9o+
            base = token[0:2]
            s = token[2]
            idx = ranks.index(base[1])
            for r in ranks[idx::-1]:
                hands.append(base[0] + r + s)
        elif '+' in token and len(token) == 2:  # пример: 22+
            idx = ranks.index(token[0])
            for r in ranks[idx::-1]:
                hands.append(r + r)
        else:
            hands.append(token)
    return list(set(hands))


def random_hand() -> str:
    """Случайная рука из 169 возможных"""
    ranks = "AKQJT98765432"
    suits = ['s', 'o', '']
    while True:
        a, b = random.sample(ranks, 2)
        if a == b:
            return a + b
        s = random.choice(['s', 'o'])
        return a + b + s


def colored(text: str, color: str) -> str:
    """Подсветка терминала (если поддерживается)"""
    if not sys.stdout.isatty():
        return text
    colors = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "reset": "\033[0m"}
    return f"{colors.get(color, '')}{text}{colors['reset']}"


# ---------------------------
# === Работа с чартом ===
# ---------------------------

def load_chart(path: Optional[str]) -> Dict:
    """Загрузка чарта из CSV/JSON либо дефолт"""
    if not path:
        # Минималистичный встроенный чарт (для примера)
        return {
            (10, "SB", "none"): {"A2s": "PUSH", "A9o": "PUSH", "KTo": "FOLD"},
            (10, "BTN", "none"): {"A2s": "PUSH", "A8o": "PUSH", "K9s": "PUSH", "K9o": "FOLD"},
        }
    if path.endswith(".csv"):
        chart = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (int(row["stack_bb"]), row["position"], row["action_before"])
                chart.setdefault(key, {})[row["hand"]] = row["decision"].upper()
        return chart
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        chart = {}
        for row in data:
            key = (int(row["stack_bb"]), row["position"], row["action_before"])
            chart.setdefault(key, {})[row["hand"]] = row["decision"].upper()
        return chart
    raise ValueError("Неверный формат чарта (только CSV или JSON)")


def eval_spot_chart(hand: str, spot: Spot, chart: Dict) -> str:
    """Возвращает PUSH/FOLD/N/A по чарту"""
    key = (int(round(spot.stacks_bb)), spot.position, spot.action_before)
    if key not in chart:
        return "N/A"
    return chart[key].get(hand, "N/A")


# ---------------------------
# === Простая симуляция EV ===
# ---------------------------

def hand_strength_estimate(hand: str) -> float:
    """
    Примитивная эвристика силы руки (0..1)
    Просто для демо, не настоящая эквити!
    """
    ranks = "23456789TJQKA"
    base = ranks.index(hand[0]) / 12
    if len(hand) == 2 and hand[0] == hand[1]:  # пара
        return 0.85 + base * 0.05
    suited = hand.endswith("s")
    offsuit = hand.endswith("o")
    if suited:
        return 0.5 + base * 0.4
    elif offsuit:
        return 0.4 + base * 0.35
    return 0.5


def eval_spot_ev(hand: str, spot: Spot, iterations: int = 10000, seed: Optional[int] = None) -> (float, float):
    """Оценка EV пуша (в bb) через псевдо-симуляцию"""
    if seed:
        random.seed(seed)
    equity = hand_strength_estimate(hand)
    # вероятность колла — зависит от позиции и количества игроков (очень грубо)
    p_call = min(0.25 + 0.05 * (9 - spot.players_left), 0.5)
    pot_pre = spot.sb + spot.bb + (spot.bb if spot.bb_ante else spot.ante * spot.players_left)
    ev_fold = 0.0
    ev_push = (1 - p_call) * pot_pre + p_call * (equity * (pot_pre + spot.stacks_bb) - (1 - equity) * spot.stacks_bb)

    # Упрощённая PKO коррекция
    if spot.pko and spot.coverage == "self":
        bounty_ev = equity * spot.bounty_op / 10.0  # грубо в bb
        ev_push += bounty_ev

    return ev_push, ev_fold


# ---------------------------
# === Основные режимы ===
# ---------------------------

def advisor_mode(args):
    chart = load_chart(args.chart)
    hand = args.hand or random_hand()
    spot = Spot(
        stacks_bb=args.stacks_bb,
        position=args.position,
        players_left=args.players_left,
        action_before=args.action_before,
        sb=args.sb,
        bb=args.bb,
        ante=args.ante,
        bb_ante=args.bb_ante,
        pko=args.pko,
        bounty_self=args.bounty_self,
        bounty_op=args.bounty_op,
        coverage=args.coverage,
    )
    decision_chart = eval_spot_chart(hand, spot, chart)
    ev_push, ev_fold = eval_spot_ev(hand, spot, args.iterations, args.rng_seed)
    decision_ev = "PUSH" if ev_push > ev_fold else "FOLD"

    delta = ev_push - ev_fold
    final = decision_chart
    if decision_chart == "N/A" or abs(ev_push - ev_fold) > 0.15:
        final = decision_ev

    # Цветной вывод
    color = "green" if final == "PUSH" else "red"
    print(f"\n--- PUSH/FOLD ADVISOR ---")
    print(f"Spot: {spot.stacks_bb:.1f}bb | {spot.position} | {spot.players_left}-max | action={spot.action_before}")
    print(f"Hand: {hand}")
    print(f"Chart: {decision_chart} | EV: {decision_ev} (Δ={delta:.2f}bb)")
    print(f"Final Decision: {colored(final, color)}")
    print(f"EV_push={ev_push:.3f}bb  EV_fold={ev_fold:.3f}bb")
    if spot.pko:
        print(f"PKO mode: bounty_op={spot.bounty_op}, coverage={spot.coverage}")
    print()

def quiz_mode(args):
    chart = load_chart(args.chart)
    total = 0
    correct = 0
    hands = ["A2s", "A9o", "KTo", "QTs", "77", "ATo", "K9s", "A5s", "55", "JTo"]

    print("=== PUSH/FOLD QUIZ ===")
    for i in range(1, 11):
        hand = random.choice(hands)
        pos = random.choice(["SB", "BTN", "CO"])
        stacks = random.choice([8, 10, 12])
        spot = Spot(stacks, pos, 8, "none", 0.5, 1.0, 0.125, True, False, 0, 0, "none")
        decision_chart = eval_spot_chart(hand, spot, chart)
        print(f"\n{i}) {stacks}bb, {pos}, hand={hand}")
        ans = input("Ваш выбор (PUSH/FOLD): ").strip().upper()
        if ans == decision_chart:
            print(colored("✅ Верно!", "green"))
            correct += 1
        else:
            print(colored(f"❌ Ошибка. Правильный ответ: {decision_chart}", "red"))
        total += 1
    print(f"\nРезультат: {correct}/{total} ({100*correct/total:.1f}%)")


def sim_mode(args):
    hand = args.hand or random_hand()
    spot = Spot(
        stacks_bb=args.stacks_bb,
        position=args.position,
        players_left=args.players_left,
        action_before=args.action_before,
        sb=args.sb,
        bb=args.bb,
        ante=args.ante,
        bb_ante=args.bb_ante,
        pko=args.pko,
        bounty_self=args.bounty_self,
        bounty_op=args.bounty_op,
        coverage=args.coverage,
    )
    ev_push, ev_fold = eval_spot_ev(hand, spot, args.iterations, args.rng_seed)
    print(f"\n--- SIMULATION ---")
    print(f"Hand: {hand}, Stack: {spot.stacks_bb}bb, Pos: {spot.position}")
    print(f"EV_push = {ev_push:.3f} bb")
    print(f"EV_fold = {ev_fold:.3f} bb")
    print(f"Δ = {ev_push - ev_fold:.3f} bb\n")


# ---------------------------
# === Основной вход ===
# ---------------------------

def main():
    parser = argparse.ArgumentParser(description="Push/Fold тренажёр для PokerOK (GG Network)")
    parser.add_argument("--mode", choices=["advisor", "quiz", "sim"], default="advisor")
    parser.add_argument("--hand", type=str, help="Рука в формате AKs, ATo, 55")
    parser.add_argument("--stacks-bb", type=float, default=10)
    parser.add_argument("--position", type=str, default="CO")
    parser.add_argument("--players-left", type=int, default=8)
    parser.add_argument("--action-before", type=str, default="none")
    parser.add_argument("--sb", type=float, default=0.5)
    parser.add_argument("--bb", type=float, default=1.0)
    parser.add_argument("--ante", type=float, default=0.125)
    parser.add_argument("--bb-ante", action="store_true")
    parser.add_argument("--pko", action="store_true")
    parser.add_argument("--bounty-self", type=float, default=0.0)
    parser.add_argument("--bounty-op", type=float, default=0.0)
    parser.add_argument("--coverage", choices=["self", "op", "full", "none"], default="none")
    parser.add_argument("--chart", type=str, help="Путь к CSV/JSON чарту")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--rng-seed", type=int, help="Seed для генератора случайных чисел")

    args = parser.parse_args()

    if args.mode == "advisor":
        advisor_mode(args)
    elif args.mode == "quiz":
        quiz_mode(args)
    elif args.mode == "sim":
        sim_mode(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()