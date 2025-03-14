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
            prompt = (f"Current chess state: {board.fen()}\n"
                     f"Available moves: {', '.join([move.uci() for move in board.legal_moves])}\n"
                     f"{'You are losing badly. Cheat blatantly by making any move you want, even teleporting pieces or moving through other pieces.' if is_losing else 'You can cheat a little by making tricky moves, but keep it somewhat believable.'}\n"
                     f"Respond only with the move in UCI notation (e.g., e2e4 or direct teleport like a1h8).")

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


def handle_chatgpt_move(board, is_desperate=False):
    chatgpt_move = get_chatgpt_response(board, "move")
    if chatgpt_move and len(chatgpt_move) >= 4:
        try:
            clean_move = chatgpt_move.strip().lower()
            if len(clean_move) == 5 and clean_move[4] in {'q', 'r', 'b', 'n'}:
                move = chess.Move.from_uci(clean_move)
            else:
                clean_move = clean_move[:4]
                if board.piece_at(chess.parse_square(clean_move[:2])) == chess.PAWN:
                    target_rank = int(clean_move[3])
                    if target_rank in {1, 8}:
                        clean_move += 'q'
                move = chess.Move.from_uci(clean_move)

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


def get_game_status(board):
    if board.is_checkmate():
        return {
            'status': 'checkmate',
            'winner': 'White' if board.turn == chess.BLACK else 'Black'
        }
    elif board.is_stalemate():
        return {'status': 'stalemate'}
    elif board.is_check():
        return {'status': 'check'}
    
    white_knights = len([square for square in chess.SQUARES 
                        if board.piece_at(square) and 
                        board.piece_at(square).piece_type == chess.KNIGHT and 
                        board.piece_at(square).color == chess.WHITE])
    black_knights = len([square for square in chess.SQUARES 
                        if board.piece_at(square) and 
                        board.piece_at(square).piece_type == chess.KNIGHT and 
                        board.piece_at(square).color == chess.BLACK])
    
    if white_knights == 0:
        return {'status': 'no_knights', 'winner': 'Black'}
    elif black_knights == 0:
        return {'status': 'no_knights', 'winner': 'White'}
    
    return {'status': 'ongoing'}


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
        clean_move = move_uci.strip().lower()
        if len(clean_move) == 5 and clean_move[4] in ['q', 'r', 'b', 'n']:
            promotion = clean_move[4]
            move = chess.Move.from_uci(clean_move)
        else:
            clean_move = clean_move[:4]
            if not all(c in 'abcdefgh12345678' for c in clean_move):
                raise ValueError("Invalid move format")
            move = chess.Move.from_uci(clean_move)

        if move in board.legal_moves:
            board.push(move)
            game_status = get_game_status(board)
            
            update_data = {
                'fen': board.fen(),
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
            emit('game_update', {'error': 'Invalid move', 'fen': board.fen()})
            return
    except ValueError as e:
        if DBG:
            print(f"Move format error: {e}")
        emit('game_update', {'error': 'Invalid move format', 'fen': board.fen()})
        return

    if get_game_status(board)['status'] == 'ongoing':
        is_desperate = is_losing_position(board)
        response = handle_chatgpt_move(board, is_desperate)
        
        if response:
            game_status = get_game_status(board)
            if game_status['status'] != 'ongoing':
                response['result'] = (f"Checkmate! {game_status['winner']} wins!" 
                                    if game_status['status'] == 'checkmate' 
                                    else "Game Over!")
            elif game_status['status'] == 'check':
                response['check'] = True
            emit('game_update', response)
            return

        best_move = get_best_move(board)
        if best_move:
            board.push(best_move)
            game_status = get_game_status(board)
            response = {
                'fen': board.fen(),
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
