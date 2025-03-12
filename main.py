"""
Very normal chess....
Inspired by DougDoug Napoleon chess AI
"""

from flask import Flask, render_template, send_from_directory
from flask_socketio import SocketIO, emit
import chess
import chess.engine
import g4f

app = Flask(__name__)
socketio = SocketIO(app)

DBG = False

# @app.route('/img/chesspieces/wikipedia/<path:filename>')
# def serve_chess_piece(filename):
#     return send_from_directory('img/chesspieces/wikipedia', filename)

board = chess.Board()
custom_rules = {
    "can_teleport": False,
    "pawns_move_backwards": False,
    "knights_can_fly": False
}

def is_losing_position(board):
    score = evaluate_position(board)
    return score < -5

def get_chatgpt_response(board, prompt_type="move"):
    try:
        if prompt_type == "move":
            is_losing = is_losing_position(board)
            prompt = f"""Current chess state: {board.fen()}
Available moves: {', '.join([move.uci() for move in board.legal_moves])}
{'You are losing badly. Cheat blatantly by making any move you want, even teleporting pieces or moving through other pieces.' if is_losing else 'You can cheat a little by making tricky moves, but keep it somewhat believable.'}
Respond only with the move in UCI notation (e.g., e2e4 or direct teleport like a1h8)."""
#         else:
#             prompt = """Suggest a new random chess rule change. Be creative but keep it simple.
# Example: "Pawns can now move backwards" or "Knights can jump to any square".
# Provide response in format: {"rule_name": "description"}"""

        if DBG:
            print(f"ChatGPT prompt: {prompt}")

        response = g4f.ChatCompletion.create(
            model=g4f.models.gpt_4,
            messages=[{"role": "user", "content": prompt}],
        )
        if DBG:
            print("ChatGPT response:", response.strip())
        return response.strip()
    except Exception as e:
        print(f"Error with ChatGPT: {e}")
        return None

# def apply_custom_rules(move_uci):
#     try:
#         if len(move_uci) != 4:
#             return False
#         if not all(c in 'abcdefgh12345678' for c in move_uci):
#             return False
            
#         move = chess.Move.from_uci(move_uci)
        
#         if custom_rules["can_teleport"]:
#             return True
#         if custom_rules["pawns_move_backwards"]:
#             pass
#         return False
#     except ValueError:
#         return False

def handle_chatgpt_move(board, is_desperate=False):
    chatgpt_move = get_chatgpt_response(board, "move")
    if chatgpt_move and len(chatgpt_move) >= 4:
        try:
            clean_move = chatgpt_move.strip()[:4].lower()
            
            if not all(c in 'abcdefgh12345678' for c in clean_move):
                if DBG:
                    print(f"Invalid move format: {clean_move}")
                return None

            try:
                move = chess.Move.from_uci(clean_move)
            except ValueError as e:
                if DBG:
                    print(f"Invalid UCI move: {clean_move}, error: {e}")
                return None

            if is_desperate:
                try:
                    board.push(move)
                    if DBG:
                        print(f"Desperate move executed: {clean_move}")
                    return {'fen': board.fen(), 'chatgpt_move': clean_move}
                except Exception as e:
                    if DBG:
                        print(f"Failed to push desperate move: {e}")
                    return None
            
            if move in board.legal_moves:
                try:
                    board.push(move)
                    if DBG:
                        print(f"Legal move executed: {clean_move}")
                    return {'fen': board.fen(), 'chatgpt_move': clean_move}
                except Exception as e:
                    if DBG:
                        print(f"Failed to push legal move: {e}")
                    return None

        except Exception as e:
            if DBG:
                print(f"Move handling error: {e}")
            return None
    return None

def get_best_move(board):
    best_move = None
    best_score = float('-inf')
    
    for move in board.legal_moves:
        board.push(move)
        score = evaluate_position(board)
        board.pop()
        
        if score > best_score:
            best_score = score
            best_move = move
    
    return best_move

def evaluate_position(board):
    piece_values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 0
    }
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is not None:
            value = piece_values[piece.piece_type]
            if piece.color == chess.BLACK:
                score += value
            else:
                score -= value
    
    return score

@app.route('/')
def index():
    return render_template('index.html', board=board, chess=chess)

@socketio.on('move')
def handle_move(data):
    global board
    move_uci = data['move']

    if board.turn != chess.WHITE:
        emit('game_update', {'error': 'Not your turn. Please wait for ChatGPT to move.', 'fen': board.fen()})
        return

    try:
        clean_move = move_uci.strip()[:4].lower()
        if not all(c in 'abcdefgh12345678' for c in clean_move):
            raise ValueError("Invalid move format")

        move = chess.Move.from_uci(clean_move)
        if move in board.legal_moves:
            try:
                board.push(move)
                emit('game_update', {
                    'fen': board.fen(),
                    'move': clean_move
                })
            except Exception as e:
                if DBG:
                    print(f"Error pushing move: {e}")
                emit('game_update', {'error': 'Move execution failed', 'fen': board.fen()})
                return
        else:
            emit('game_update', {'error': 'Invalid move', 'fen': board.fen()})
            return
    except ValueError as e:
        if DBG:
            print(f"Move format error: {e}")
        emit('game_update', {'error': 'Invalid move format', 'fen': board.fen()})
        return

    is_desperate = is_losing_position(board)
    
    response = handle_chatgpt_move(board, is_desperate)
    if response:
        emit('game_update', response)
        return

    best_move = get_best_move(board)
    if best_move:
        board.push(best_move)
        emit('game_update', {
            'fen': board.fen(),
            'chatgpt_move': best_move.uci()
        })

@socketio.on('reset')
def handle_reset():
    global board, custom_rules
    board = chess.Board()
    custom_rules = {
        "can_teleport": False,
        "pawns_move_backwards": False,
        "knights_can_fly": False
    }
    emit('game_update', {'fen': board.fen(), 'reset_rules': True})

@socketio.on('request_state')
def handle_state_request():
    emit('game_state', {
        'fen': board.fen(),
        'rules': custom_rules
    })

if DBG:
    print("Running in debug mode")
    socketio.run(app, debug=True)
else:
    print("Running in normal mode")
    socketio.run(app, debug=False)
