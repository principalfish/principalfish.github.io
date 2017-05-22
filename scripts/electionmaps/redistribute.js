var redistribute = {
  active: false,

  validInput : true,

  ukip: false,
  green : false,

  values : {

    "ukip" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100},
    "green" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100}
  },

  check  : function(partyFrom, partyTo, value){

    valueInt = parseInt(value);

    if (Number.isInteger(valueInt)){
      this.values[partyFrom][partyTo] = valueInt;
    } else {
      this.values[partyFrom][partyTo] = 0;
    }

    var notVoting = this.values[partyFrom].notVoting;
    var sumUserInput = this.values[partyFrom].conservative + this.values[partyFrom].labour + this.values[partyFrom].libdems;
    this.values[partyFrom].notVoting = 100 - sumUserInput;

    var notVotingSpan =   this.values[partyFrom].notVoting

    this.validInput = true;

    if (notVotingSpan < 0){
      notVotingSpan = "<0";
      this.validInput = false;
    }

    $("#redist-" + partyFrom + "-notvoting" ).text(notVotingSpan);

    if (this.values[partyFrom].notVoting != 100){
      this[partyFrom] = true;
    } else {
      this[partyFrom] = false;
    }

  },

  handle : function(){
    if (!(this.validInput)){
      alert("Percentages add up to more than 100!");
    }
    else {
      this.reset()
      $.each(this.values, function(partyFrom, values){
        if (redistribute[partyFrom] == true){
          $.each(currentMap.seatData, function(seat, data){

            if (partyFrom in data.partyInfo){

              if (data.partyInfo[partyFrom].standing == 0){

                redistribute.calculate(data, partyFrom, values)
              }
            }
          })
        }
      })
      filters.reset();
    }
  },

  calculate : function(data, partyFrom, values){

    var currentVote = data.partyInfo[partyFrom].total;
    data.seatInfo.turnout -= (currentVote * values.notVoting / 100);
    data.seatInfo.turnout = Math.round(data.seatInfo.turnout);

    $.each(values, function(party, voteAdded){
      if (party in data.partyInfo){
        data.partyInfo[party].total += voteAdded * currentVote / 100;
        data.partyInfo[party].total = Math.round(data.partyInfo[party].total);
      }
    });

    delete data.partyInfo[partyFrom];
    // recalculate majority + current
    var maxParty = null;
    var maxVotes = 0 ;
    var votes = []
    $.each(data.partyInfo, function(party, data){
      votes.push(data.total);
      if (data.total > maxVotes){
        maxVotes = data.total;
        maxParty = party ;
      }
    });

    votes.sort(function(a, b){
      return b > a;
    })

    data.seatInfo.current = maxParty;
    data.seatInfo.majority = votes[0] - votes[1]

  },

  reset : function(){
    currentMap.seatData = jQuery.extend(true, {}, currentMap.seatDataBackup);
    filters.reset();
  },

  resetInputs : function(){
    $("#redist-table").find("input:text").val(0);
    $("#redist-ukip-notvoting").text("100");
    $("#redist-green-notvoting").text("100");

    this.green = false;
    this.ukip = false;

    this.values = {
      "ukip" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100},
      "green" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100}
    }

    this.reset()
  }
}
