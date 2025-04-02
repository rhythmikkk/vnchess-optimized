from flask import Flask, render_template, send_from_directory
from flask_socketio import SocketIO, emit
import chess
import chess.engine
import g4f
from dataclasses import dataclass
from typing import Optional, Dict, Any

app = Flask(__name__)
socketio = SocketIO(app)

DBG = True

@dataclass
class GameState:
    board: chess.Board
    custom_rules: Dict[str, bool]

    @classmethod
    def new_game(cls):
        return cls(
            board=chess.Board(),
            custom_rules={
                "can_teleport": False,
                "pawns_move_backwards": False,
                "knights_can_fly": False
            }
        )

game_state = GameState.new_game()

def evaluate_position(board: chess.Board) -> int:
    piece_values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 0
    }
    
    return sum(
        piece_values[piece.piece_type] * (-1 if piece.color == chess.WHITE else 1)
        for square in chess.SQUARES
        if (piece := board.piece_at(square)) is not None
    )

def is_losing_position(board: chess.Board) -> bool:
    return evaluate_position(board) < -5

def get_chatgpt_response(board: chess.Board, prompt_type: str = "move") -> Optional[str]:
    try:
        if prompt_type != "move":
            return None

        is_losing = is_losing_position(board)
        prompt = (
            f"Current chess state: {board.fen()}\n"
            f"Available moves: {', '.join(move.uci() for move in board.legal_moves)}\n"
            f"{'You are losing badly. Cheat blatantly by making any move you want, even teleporting pieces or moving through other pieces.' if is_losing else 'You can cheat a little by making tricky moves, but keep it somewhat believable.'}\n"
            f"Respond only with the move in UCI notation (e.g., e2e4 or direct teleport like a1h8)."
        )

        if DBG:
            print(f"ChatGPT prompt: {prompt}")

        response = g4f.ChatCompletion.create(
            model=g4f.models.gpt_4,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.strip()
    except Exception as e:
        if DBG:
            print(f"Error with ChatGPT: {e}")
        return None

def validate_move(move_str: str) -> Optional[tuple[str, bool]]:
    """Validates move format and returns (cleaned_move, is_promotion)"""
    if not move_str or len(move_str) < 4:
        return None
    
    clean_move = move_str.strip().lower()
    
    # Basic format validation
    if not all(c in 'abcdefgh12345678qrbn' for c in clean_move):
        return None
        
    # Check promotion format
    is_promotion = len(clean_move) == 5 and clean_move[4] in 'qrbn'
    if len(clean_move) == 5 and not is_promotion:
        return None
        
    return clean_move, is_promotion

def handle_chatgpt_move(board: chess.Board, is_desperate: bool = False) -> Optional[Dict[str, str]]:
    chatgpt_move = get_chatgpt_response(board, "move")
    validated = validate_move(chatgpt_move)
    if not validated:
        return None
        
    clean_move, is_promotion = validated
    
    try:
        # Auto-promote pawns to queen when reaching the end
        if not is_promotion:
            from_square = chess.parse_square(clean_move[:2])
            if (board.piece_at(from_square) == chess.PAWN and 
                int(clean_move[3]) in {1, 8}):
                clean_move += 'q'
                
        move = chess.Move.from_uci(clean_move)
        
        # Handle desperate or legal moves
        if is_desperate or move in board.legal_moves:
            board.push(move)
            return {'fen': board.fen(), 'chatgpt_move': clean_move}
            
    except Exception as e:
        if DBG:
            print(f"Move error: {e}")
    return None

def get_game_status(board: chess.Board) -> Dict[str, str]:
    if board.is_checkmate():
        return {
            'status': 'checkmate',
            'winner': 'White' if board.turn == chess.BLACK else 'Black'
        }
    elif board.is_stalemate():
        return {'status': 'stalemate'}
    elif board.is_check():
        return {'status': 'check'}
    return {'status': 'ongoing'}

@app.route('/')
def index():
    return render_template('index.html', board=game_state.board, chess=chess)

@socketio.on('move')
def handle_move(data):
    move_uci = data['move']

    if game_state.board.turn != chess.WHITE:
        emit('game_update', {'error': 'Not your turn. Please wait for ChatGPT to move.', 'fen': game_state.board.fen()})
        return

    try:
        clean_move = move_uci.strip().lower()
        if (len(clean_move) == 5 and clean_move[4] in ['q', 'r', 'b', 'n'] and
            game_state.board.piece_at(chess.parse_square(clean_move[:2])) and
            game_state.board.piece_at(chess.parse_square(clean_move[:2])).piece_type == chess.PAWN):
            move = chess.Move.from_uci(clean_move)
        else:
            clean_move = clean_move[:4]
            if not all(c in 'abcdefgh12345678' for c in clean_move):
                raise ValueError("Invalid move format")
            move = chess.Move.from_uci(clean_move)

        if move in game_state.board.legal_moves:
            game_state.board.push(move)
            game_status = get_game_status(game_state.board)
            
            update_data = {
                'fen': game_state.board.fen(),
                'move': clean_move
            }

            if game_status['status'] == 'checkmate':
                update_data['result'] = f"Checkmate! {game_status['winner']} wins!"
                emit('game_update', update_data)
                return
            elif game_status['status'] == 'stalemate':
                update_data['result'] = "Game Over! Stalemate!"
                emit('game_update', update_data)
                return
            elif game_status['status'] == 'no_knights':
                update_data['result'] = f"Game Over! {game_status['winner']} wins by eliminating all enemy knights!"
                emit('game_update', update_data)
                return
            elif game_status['status'] == 'check':
                update_data['check'] = True
            
            emit('game_update', update_data)
        else:
            emit('game_update', {'error': 'Invalid move', 'fen': game_state.board.fen()})
            return
    except ValueError as e:
        if DBG:
            print(f"Move format error: {e}")
        emit('game_update', {'error': 'Invalid move format', 'fen': game_state.board.fen()})
        return

    if get_game_status(game_state.board)['status'] == 'ongoing':
        is_desperate = is_losing_position(game_state.board)
        response = handle_chatgpt_move(game_state.board, is_desperate)
        
        if response:
            game_status = get_game_status(game_state.board)
            if game_status['status'] != 'ongoing':
                response['result'] = (f"Checkmate! {game_status['winner']} wins!" 
                                    if game_status['status'] == 'checkmate' 
                                    else "Game Over!")
            elif game_status['status'] == 'check':
                response['check'] = True
            emit('game_update', response)
            return

        best_move = get_best_move(game_state.board)
        if best_move:
            game_state.board.push(best_move)
            game_status = get_game_status(game_state.board)
            response = {
                'fen': game_state.board.fen(),
                'chatgpt_move': best_move.uci()
            }
            if game_status['status'] != 'ongoing':
                response['result'] = (f"Checkmate! {game_status['winner']} wins!" 
                                    if game_status['status'] == 'checkmate' 
                                    else "Game Over!")
            elif game_status['status'] == 'check':
                response['check'] = True
            emit('game_update', response)

@socketio.on('reset')
def handle_reset():
    global game_state
    game_state = GameState.new_game()
    emit('game_update', {
        'fen': game_state.board.fen(),
        'reset': True,
        'message': 'Game has been reset',
        'rules': game_state.custom_rules
    }, broadcast=True)  # Added broadcast to ensure all clients get updated

@socketio.on('request_state')
def handle_state_request():
    emit('game_state', {
        'fen': game_state.board.fen(),
        'rules': game_state.custom_rules
    })

if DBG:
    print("Running in debug mode")
    socketio.run(app, debug=True)
else:
    print("Running in normal mode")
    socketio.run(app, debug=False)
